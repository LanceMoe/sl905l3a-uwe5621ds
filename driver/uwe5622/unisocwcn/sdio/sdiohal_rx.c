#include "sdiohal.h"

static unsigned int sdiohal_rx_adapt_get(void)
{
    struct sdiohal_data_t *p_data = sdiohal_get_data();
#ifdef CONFIG_MESON_UWE5621
    unsigned int limit = sdiohal_rx_packet_limit();

    if (limit)
        return min(p_data->dtbs, limit);
#endif
    return p_data->dtbs;
}

static void sdiohal_rx_adapt_set_dtbs(unsigned int len)
{
	struct sdiohal_data_t *p_data = sdiohal_get_data();
	unsigned int off;

	if (len == 0) {
		#ifdef CONFIG_MESON_UWE5621
        /* Empty/unknown queues need no large transfer. Escalate only from
         * the actual pending count returned by the previous packet. */
        p_data->dtbs = min_t(unsigned int, MAX_PAC_SIZE,
                             sdiohal_packet_limit());
#else
        p_data->dtbs = MAX_PAC_SIZE;
#endif
		return;
	}

	off = (len >> 10) + 1;
	len = SDIOHAL_ALIGN_BLK(len + 8 * off + 64);
#if (BITS_PER_LONG > 32) || (PAGE_SIZE >= 65536)
	p_data->dtbs = (len >= SDIOHAL_RX_RECVBUF_LEN) ?
			SDIOHAL_RX_RECVBUF_LEN : len;
#else
	p_data->dtbs = (len >= SDIOHAL_32_BIT_RX_RECVBUF_LEN) ?
			SDIOHAL_32_BIT_RX_RECVBUF_LEN : len;
#endif
}

static unsigned int sdiohal_rx_adapt_get_pac_num(void)
{
	struct sdiohal_data_t *p_data = sdiohal_get_data();

	return p_data->remain_pac_num;
}

static void sdiohal_rx_adapt_set_pac_num(unsigned int num)
{
	struct sdiohal_data_t *p_data = sdiohal_get_data();

	if (num == 0) {
		p_data->remain_pac_num = 1;
		return;
	}

	p_data->remain_pac_num =
		(num >= MAX_CHAIN_NODE_NUM) ? MAX_CHAIN_NODE_NUM : num;
}

static int sdiohal_data_list_assignment(struct mbuf_t *mbuf_node,
	struct sdio_puh_t *puh, int channel)
{
	struct sdiohal_list_t *rx_channel_list;

	rx_channel_list = sdiohal_get_rx_channel_list(channel);
	if (rx_channel_list->node_num == 0)
		rx_channel_list->mbuf_head = mbuf_node;
	else
		rx_channel_list->mbuf_tail->next = mbuf_node;

	mbuf_node->next = NULL;
	rx_channel_list->mbuf_tail = mbuf_node;
	rx_channel_list->type = puh->type;
	rx_channel_list->subtype = puh->subtype;
	rx_channel_list->node_num++;

	return 0;
}

/* for adma */
static int sdiohal_rx_list_parser(struct sdiohal_list_t *data_list,
				int valid_len)
{
	struct sdiohal_data_t *p_data = sdiohal_get_data();
	struct sdio_puh_t *puh = NULL;
	struct mbuf_t *mbuf_node, *mbuf_next;
	unsigned int node_num, i;
	int inout = 0, channel = 0;
	unsigned int parse_len;

	sdiohal_list_check(data_list, __func__, SDIOHAL_READ);

	node_num = data_list->node_num;
	mbuf_next = data_list->mbuf_head;
	parse_len = 0;
	for (i = 0; i < node_num; i++) {
		mbuf_node = mbuf_next;
		mbuf_next = mbuf_next->next;
		puh = (struct sdio_puh_t *)mbuf_node->buf;
		if ((puh->eof == 0) && (puh->type != 0xF)) {
			channel = sdiohal_hwtype_to_channel(inout,
				puh->type, puh->subtype);
			parse_len += puh->len;
			if (puh->check_sum)
				puh->len += 2;
			if ((channel >= SDIO_CHANNEL_NUM) || (puh->len >
				(MAX_PAC_SIZE - SDIO_PUB_HEADER_SIZE)) ||
				(puh->len == 0)) {
				sdiohal_rx_list_free(mbuf_node, mbuf_node, 1);
				sdiohal_err("%s skip type[%d]sub[%d]len[%d]\n",
					    __func__, puh->type, puh->subtype,
					    puh->len);
				continue;
			}
			p_data->rx_packer_cnt++;
			mbuf_node->len = MAX_MBUF_SIZE;
			sdiohal_data_list_assignment(mbuf_node, puh, channel);
		} else {
			sdiohal_debug("%s eof pac:%d,parse[%d]%s valid[%d]\n",
				      __func__, puh->eof, parse_len,
				      (parse_len < valid_len ? "<":">="),
				      valid_len);
			sdiohal_debug("%s type[%d]sub[%d]len[%d]\n", __func__,
				      puh->type, puh->subtype, puh->len);

			sdiohal_rx_list_free(mbuf_node, mbuf_node, 1);
		}
	}

	return 0;
}

/* for normal dma */
static int sdiohal_rx_dispatch_span(char *data_buf, unsigned int offset,
                                   unsigned int span, unsigned int wire_len)
{
    struct sdiohal_data_t *p_data = sdiohal_get_data();
    struct sdio_puh_t *puh = (struct sdio_puh_t *)(data_buf + offset);
    struct sdiohal_list_t *data_list;
    int channel;

    if (puh->type == 0xF)
        return 0;
    channel = sdiohal_hwtype_to_channel(0, puh->type, puh->subtype);
    if (channel < SDIO_CHN_TX_NUM || channel >= SDIO_CHANNEL_NUM ||
        !puh->len || wire_len > MAX_PAC_SIZE - SDIO_PUB_HEADER_SIZE)
        return -EPROTO;
    data_list = sdiohal_get_rx_mbuf_node(1);
    if (!data_list)
        return -ENOMEM;
    puh->len = wire_len;
    data_list->mbuf_head = data_list->mbuf_tail;
    data_list->mbuf_head->buf = (unsigned char *)puh;
    data_list->mbuf_head->len = wire_len;
    data_list->node_num = 1;
    p_data->frag_ctl.pagecnt_bias--;
    p_data->rx_packer_cnt++;
    sdiohal_print_list_data(channel, data_list, __func__, SDIOHAL_DATA_LEVEL);
    sdiohal_data_list_assignment(data_list->mbuf_head, puh, channel);
    kfree(data_list);
    return 0;
}

static int sdiohal_rx_buf_parser(char *data_buf, unsigned int valid_len,
                                unsigned int buffer_len)
{
    unsigned int offset = 0, parsed = 0, payload, wire_len, span;
    struct sdio_puh_t *puh;
    int ret;

    /* The CP trailer can describe bytes beyond this CMD53. Dispatch every
     * packet wholly inside the buffer, then stop. Do not fail the read:
     * a following oversized head has not been consumed. */
    if (valid_len > buffer_len)
        valid_len = buffer_len;
    while (parsed < valid_len) {
        if (buffer_len - offset < sizeof(*puh))
            return 0;
        puh = (struct sdio_puh_t *)(data_buf + offset);
        if (puh->eof)
            return 0;
        payload = puh->len;
        wire_len = payload + (puh->check_sum ? 2 : 0);
        span = sizeof(*puh) + SDIOHAL_ALIGN_4BYTE(wire_len);
        if (!payload || span > buffer_len - offset ||
            parsed + payload > valid_len)
            return 0;
        ret = sdiohal_rx_dispatch_span(data_buf, offset, span, wire_len);
        if (ret)
            return ret;
        parsed += payload;
        offset += span;
    }
    return 0;
}

/* Never interpret a previous FIFO read's trailer after an I/O failure. */
static int sdiohal_sdma_rx_packet(char *buffer, unsigned int length,
                                unsigned int *valid, unsigned int *pending)
{
    int ret;

    *valid = 0;
    *pending = 0;
    if (length < 8)
        return -EINVAL;
    ret = sdiohal_sdio_pt_read((unsigned char *)buffer, length);
    if (ret)
        return ret;
    *pending = *((unsigned int *)(buffer + length - 4));
    *valid = *((unsigned int *)(buffer + length - 8));
    if (*valid > length - 8) {
        pr_err_ratelimited("sdiohal: RX truncated (read=%u valid=%u next=%u)\n",
                           length, *valid, *pending);
    }
    ret = sdiohal_rx_buf_parser(buffer, *valid, length - 8);
    if (ret) {
        pr_err_ratelimited("sdiohal: RX framing error %d (read=%u valid=%u next=%u)\n",
                           ret, length, *valid, *pending);
        *pending = 0;
    }
    return ret;
}

#ifdef CONFIG_MESON_UWE5621
/* 1: drain again; 0: wait normally; -1: yield before another opportunity.
 * A persistent nonzero pending count with zero payload is not progress.
 * Do not reset CP or assume that the head packet has been consumed.
 */
static int sdiohal_rx_drain_next(int status, unsigned int valid,
                               unsigned int pending, unsigned int *reads)
{
    if (status)
        return -1;
    if (!pending)
        return 0;
    if (!valid) {
        sdiohal_rx_no_progress();
        pr_err_ratelimited("sdiohal: RX no progress (pending=%u); backing off\n", pending);
        return -1;
    }
    if (++*reads >= 64)
        return -1;
    return 1;
}
#endif

static void sdiohal_rx_wait(void)
{
	struct sdiohal_data_t *p_data = sdiohal_get_data();

	while (1) {
		msleep(300);
		if (WCN_CARD_EXIST(&p_data->xmit_cnt))
			break;
	}
}

int sdiohal_rx_thread(void *data)
{
	struct sdiohal_data_t *p_data = sdiohal_get_data();
	struct sched_param param;
	int read_len, mbuf_num;
	int ret = 0;
	unsigned int rx_dtbs = 0;
	unsigned int valid_len = 0;
	static char *rx_buf;
	struct sdiohal_list_t *data_list = NULL;
	struct timespec tm_begin, tm_end;
	static long time_total_ns;
	static int times_count;
#ifdef CONFIG_MESON_UWE5621
    unsigned int drained;
    int drain_action;
#endif

	param.sched_priority = SDIO_RX_TASK_PRIO;
	sched_set_fifo_low(current);
	sdiohal_rx_adapt_set_dtbs(0);
	sdiohal_rx_adapt_set_pac_num(1);

	while (1) {
		/* Wait the semaphore */
		if (p_data->irq_type == SDIOHAL_RX_POLLING)
			sdiohal_rx_wait();
		else
			sdiohal_rx_down();
		if (p_data->exit_flag)
			break;
		if (!WCN_CARD_EXIST(&p_data->xmit_cnt)) {
			sdiohal_err("%s line %d not have card\n",
				    __func__, __LINE__);
			continue;
		}

		getnstimeofday(&p_data->tm_end_irq);
		sdiohal_pr_perf("rx sch time:%ld\n",
				(long)(timespec_to_ns(&p_data->tm_end_irq)
				- timespec_to_ns(&p_data->tm_begin_irq)));

		sdiohal_resume_wait();
		sdiohal_cp_rx_wakeup(PACKER_RX);
#ifdef CONFIG_MESON_UWE5621
        drained = 0;
#endif

read_again:
        ret = 0;
        valid_len = 0;
        rx_dtbs = 0;
		getnstimeofday(&tm_begin);

		if (p_data->adma_rx_enable) {
			/* read len is packet num */
			mbuf_num = sdiohal_rx_adapt_get_pac_num();
			sdiohal_debug("%s mbuf_num:%d adma_rx_enable:%d\n",
				      __func__, mbuf_num,
				      p_data->adma_rx_enable);

			data_list = sdiohal_get_rx_mbuf_list(mbuf_num);
			if (!data_list) {
				sdiohal_err("sdiohal_get_rx_mbuf_list fail\n");
				msleep(100);
				goto submit_list;
			}
			if (p_data->irq_type == SDIOHAL_RX_POLLING)
				memset(p_data->dtbs_buf, 0x0,
				       SDIOHAL_DTBS_BUF_SIZE);
			ret = sdiohal_adma_pt_read(data_list);
			if (ret != 0) {
				sdiohal_err("adma read fail ret:%d\n", ret);
				rx_dtbs = 0;
				if (p_data->irq_type != SDIOHAL_RX_POLLING)
					goto submit_list;
			}
			rx_dtbs =  *((unsigned int *)(p_data->dtbs_buf
				   + (SDIOHAL_DTBS_BUF_SIZE - 4)));
			valid_len = *((unsigned int *)(p_data->dtbs_buf
				    + (SDIOHAL_DTBS_BUF_SIZE - 8)));
			sdiohal_debug("%s rx_pac_num:%d, valid len:%d\n",
				      __func__, rx_dtbs, valid_len);
			sdiohal_rx_list_parser(data_list, valid_len);
			kfree(data_list);
			data_list = NULL;
		} else {
			unsigned int alloc_size;

			/* read len is packet data len */
			read_len = sdiohal_rx_adapt_get();
			sdiohal_debug("%s read_len:%d adma_rx_enable:%d\n",
				      __func__, read_len,
				      p_data->adma_rx_enable);

			rx_buf = sdiohal_get_rx_free_buf(&alloc_size);
			if (!rx_buf) {
				sdiohal_err("get_rx_free_buf fail, rlen=%d\n",
					    read_len);
				msleep(100);
				goto submit_list;
			}
			if (alloc_size < read_len) {
				read_len = alloc_size;
				sdiohal_debug("alloc_size=%d < read_len=%d\n",
					      alloc_size, read_len);
			}
#ifdef CONFIG_MESON_UWE5621
            /* Firmware startup can wake RX before a block-sized transfer
             * exists. A short read is not a card error and must not abort. */
            if (read_len < 8 || read_len % SDIOHAL_BLK_SIZE) {
                ret = 0;
                valid_len = 0;
                rx_dtbs = 0;
                goto submit_list;
            }
#endif

            ret = sdiohal_sdma_rx_packet(rx_buf, read_len, &valid_len, &rx_dtbs);
            sdiohal_debug("%s rx_dtbs:%d,valid len:%d\n",
                          __func__, rx_dtbs, valid_len);
            if (ret)
                sdiohal_err("sdio RX packet failed ret:%d\n", ret);
		}

submit_list:
		getnstimeofday(&tm_end);
		time_total_ns += timespec_to_ns(&tm_end)
			- timespec_to_ns(&tm_begin);
		times_count++;
		if (!(times_count % PERFORMANCE_COUNT)) {
			sdiohal_pr_perf("rx list avg time:%ld\n",
					(time_total_ns / PERFORMANCE_COUNT));
			time_total_ns = 0;
			times_count = 0;
		}

		sdiohal_rx_list_dispatch();
		if (p_data->adma_rx_enable)
			sdiohal_rx_adapt_set_pac_num(rx_dtbs);
		else
			sdiohal_rx_adapt_set_dtbs(rx_dtbs);
#ifdef CONFIG_MESON_UWE5621
        drain_action = sdiohal_rx_drain_next(ret, valid_len, rx_dtbs, &drained);
        if (drain_action > 0 && !READ_ONCE(p_data->exit_flag))
            goto read_again;
#else
		if (rx_dtbs > 0)
			goto read_again;
#endif

		sdiohal_cp_rx_sleep(PACKER_RX);
		sdiohal_unlock_rx_ws();
		if (p_data->irq_type != SDIOHAL_RX_POLLING)
			sdiohal_enable_rx_irq();
#ifdef CONFIG_MESON_UWE5621
        if (drain_action < 0) {
            /* Release wake accounting before sleeping. IRQ completions can
             * already be queued, so a timed completion wait alone is not
             * enough to stop a zero-payload busy loop.
             */
            msleep(SDIOHAL_RX_POLL_MS);
            sdiohal_rx_up();
        }
#endif
	}

	return 0;
}
