/* 将辨识状态与结果封装为串口前台快照；格式化及实际发送由PortalSCI负责。 */
#include "cap_sci.h"
#include "cap_monitor.h"
#include "portal_sci.h"

#if defined(__TI_COMPILER_VERSION__)
#pragma DATA_SECTION(g_capTxText, ".cap_capture")
#endif
char g_capTxText[256];
volatile uint16_t g_capSciEnabled=0U;
volatile uint32_t g_capTxFrames=0UL, g_capTxDropped=0UL;

/* 初始化发送诊断，g_capTxText用于保存最近CAP帧文本。 */
void CapSCI_Init(void)
{
    g_capSciEnabled=0U;g_capTxFrames=0UL;g_capTxDropped=0UL;
    g_capTxText[0]='\0';
}
/* 只在主循环调用；复制同一辨识事件的状态、采样统计和四路结果。
 * 非DONE或存在故障时撤销快照Valid，防止旧数组被当作新结果发送。 */
void CapSCI_Publish(void)
{
    PortalCapSnapshot snapshot;
    uint16_t i;
    snapshot.sequence=g_capSequence;snapshot.error_count=g_capErrorCount;
    snapshot.state=g_capState;snapshot.valid=g_capValid;
    snapshot.fault=g_capFault;snapshot.warning=g_capWarning;
    snapshot.sample_count=g_capSampleCount;
    snapshot.sample_rate_hz=g_capSampleRateHz;snapshot.latency_us=g_capLatencyUs;
    for(i=0U;i<4U;i++) {
        snapshot.capacitance_uF[i]=g_capC_uF[i];
        snapshot.esr_mOhm[i]=g_capESR_mOhm[i];
    }
                                                                         
                                                                           
    if(snapshot.state!=CAP_DONE || snapshot.fault!=0U)snapshot.valid=0U;
    PortalSCI_publishCap(&snapshot);
}
/* 同步PortalSCI的就绪状态和CAP帧计数；不直接访问SCI发送寄存器。 */
void CapSCI_Task(void)
{
    g_capSciEnabled=g_portalReady;
    g_capTxFrames=g_portalCapFramesSent;g_capTxDropped=g_portalCapDropped;
}
