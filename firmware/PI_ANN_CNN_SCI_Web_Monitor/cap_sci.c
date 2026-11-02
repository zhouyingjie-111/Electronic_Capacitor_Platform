#include "cap_sci.h"
#include "cap_monitor.h"
#include "portal_sci.h"

#if defined(__TI_COMPILER_VERSION__)
#pragma DATA_SECTION(g_capTxText, ".cap_capture")
#endif
char g_capTxText[256];
volatile uint16_t g_capSciEnabled=0U;
volatile uint32_t g_capTxFrames=0UL, g_capTxDropped=0UL;

void CapSCI_Init(void)
{
    g_capSciEnabled=0U;g_capTxFrames=0UL;g_capTxDropped=0UL;
    g_capTxText[0]='\0';
}
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
    /* Failed/cancelled captures keep old arrays in CCS. Only CAPSTAT may
     * describe those events: never serialize old values as a fresh CAP. */
    if(snapshot.state!=CAP_DONE || snapshot.fault!=0U)snapshot.valid=0U;
    PortalSCI_publishCap(&snapshot);
}
void CapSCI_Task(void)
{
    g_capSciEnabled=g_portalReady;
    g_capTxFrames=g_portalCapFramesSent;g_capTxDropped=g_portalCapDropped;
}
