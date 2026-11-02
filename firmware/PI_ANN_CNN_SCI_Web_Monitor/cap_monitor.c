#include "cap_monitor.h"
#include "cap_model.h"
#include "cap_port.h"
#include "cap_sci.h"
#include "main.h"
#include <math.h>

#if defined(__TI_COMPILER_VERSION__)
#pragma DATA_SECTION(samples, ".cap_capture")
#endif
/* ISR is the sole writer. It freezes the buffer before publishing WAVELET.
 * No ring-buffer overrun, no dynamic allocation and no floating-point
 * preprocessing in the ADC ISR. 1024*3*16 bits = 6144 octets. */
static volatile uint16_t samples[1024][3];
volatile uint16_t g_capEnable=1U, g_capRequest=0U, g_capState=CAP_IDLE;
volatile uint16_t g_capValid=0U, g_capFault=0U, g_capWarning=3U;
volatile uint16_t g_capSampleCount=0U;
volatile uint32_t g_capSequence=0UL, g_capErrorCount=0UL;
volatile uint32_t g_capAdcOverflowCount=0UL;
volatile float g_capC_uF[4]={0}, g_capESR_Ohm[4]={0}, g_capESR_mOhm[4]={0};
volatile float g_capSampleRateHz=0, g_capMaxSampleGapUs=0;
volatile float g_capPreprocessUs=0, g_capInferUs=0, g_capTaskMaxUs=0;
volatile float g_capBackgroundMaxUs=0;
volatile float g_capLatencyUs=0, g_capAdcIsrMaxUs=0;
volatile float g_capInputMin[3], g_capInputMax[3], g_capInputMean[3];
volatile float g_capOffsetUsed[3], g_capGainUsed[3];
static volatile uint32_t first_tick,last_tick,max_gap,expected_ticks;
static volatile uint16_t pending_error;
static uint16_t process_index;
static uint32_t clock_hz;
static uint32_t arm_tick;
static float output[8], input_sum[3], us_per_tick;

static void fail(uint16_t reason)
{
    g_capFault|=reason; g_capValid=0U;
    g_capState=CAP_ERROR; pending_error=1U;
}

void CapMonitor_Init(void)
{
    g_capEnable=1U; g_capRequest=0U; g_capState=CAP_IDLE;
    g_capValid=0U; g_capFault=0U; g_capWarning=3U;
    pending_error=0U; Cap_Reset();
}

void CapMonitor_OnAdc(uint16_t v, uint16_t ic, uint16_t io,
                      uint32_t tick, uint16_t overflow)
{
    uint16_t n;
    uint32_t gap;
    if (overflow) g_capAdcOverflowCount++;
    if (g_capState!=CAP_CAPTURE) return;
    if (!g_capEnable) { fail(CAP_ERR_CANCEL); return; }
    if (overflow) { fail(CAP_ERR_ADC_OVERFLOW); return; }
    if (v==0U || v>=4095U || ic==0U || ic>=4095U || io==0U || io>=4095U) {
        fail(CAP_ERR_ADC_RAIL); return;
    }
    n=g_capSampleCount;
    if (n==0U) first_tick=tick;
    else {
        gap=(uint32_t)(last_tick-tick);
        if (gap>max_gap) max_gap=gap;
        /* Catch a missed interrupt, debugger stop or severe service jitter. */
        if (gap<expected_ticks/2UL || gap>expected_ticks+expected_ticks/2UL) {
            g_capMaxSampleGapUs=(float)gap*us_per_tick;
            fail(CAP_ERR_TIMING); return;
        }
    }
    last_tick=tick;
    samples[n][0]=v; samples[n][1]=ic; samples[n][2]=io;
    g_capSampleCount=n+1U;
    if (n==1023U) g_capState=CAP_WAVELET;
}

static void arm(uint16_t ready)
{
    int c;
    g_capRequest=0U; g_capValid=0U; g_capFault=0U; g_capWarning=3U;
    g_capSampleCount=0U; max_gap=0UL; process_index=0U;
    g_capSampleRateHz=0; g_capMaxSampleGapUs=0;
    g_capPreprocessUs=0; g_capInferUs=0; g_capTaskMaxUs=0;
    g_capLatencyUs=0;
    if (!ready) { fail(CAP_ERR_NOT_READY); return; }
    clock_hz=CapPort_Clock();
    if (clock_hz==0UL) { fail(CAP_ERR_CONFIG); return; }
    expected_ticks=clock_hz/100000UL;
    us_per_tick=1000000.0f/(float)clock_hz;
    g_capOffsetUsed[0]=ADC_Sample_Offset.Vs1;
    g_capOffsetUsed[1]=ADC_Sample_Offset.ic;
    g_capOffsetUsed[2]=ADC_Sample_Offset.io;
    g_capGainUsed[0]=ADC_Sample_Gain.Vs1;
    g_capGainUsed[1]=ADC_Sample_Gain.ic;
    g_capGainUsed[2]=ADC_Sample_Gain.io;
    for (c=0; c<3; c++) {
        if (!isfinite(g_capOffsetUsed[c]) || !isfinite(g_capGainUsed[c]) ||
            g_capGainUsed[c]==0.0f) { fail(CAP_ERR_CONFIG); return; }
        input_sum[c]=0.0f; g_capInputMin[c]=3.402823466e38f;
        g_capInputMax[c]=-3.402823466e38f; g_capInputMean[c]=0;
    }
    Cap_Reset();
    arm_tick=CapPort_Timer();
    /* Publish LAST, so the ISR can never observe half-initialized state. */
    g_capState=CAP_CAPTURE;
}

void CapMonitor_Task(uint16_t ready)
{
    uint32_t start, elapsed;
    uint16_t old_state, batch, c;
    int rc;
    float value[3], dt;
    if (pending_error) {
        pending_error=0U; g_capErrorCount++; CapSCI_Publish();
    }
    if (!g_capEnable) {
        if (g_capState>=CAP_CAPTURE && g_capState<=CAP_CNN) fail(CAP_ERR_CANCEL);
        return;
    }
    if (g_capState==CAP_IDLE || g_capState==CAP_DONE || g_capState==CAP_ERROR) {
        if (g_capRequest) arm(ready);
        return;
    }
    if (g_capState==CAP_CAPTURE) {
        if ((uint32_t)(arm_tick-CapPort_Timer())>clock_hz/10UL) fail(CAP_ERR_TIMING);
        return;
    }
    old_state=g_capState; start=CapPort_Timer();
    if (old_state==CAP_WAVELET) {
        if (process_index==0U) {
            elapsed=(uint32_t)(first_tick-last_tick);
            if (!elapsed) { fail(CAP_ERR_TIMING); return; }
            g_capSampleRateHz=(float)clock_hz*1023.0f/(float)elapsed;
            g_capMaxSampleGapUs=(float)max_gap*us_per_tick;
            if (g_capSampleRateHz<98000.0f || g_capSampleRateHz>102000.0f) {
                fail(CAP_ERR_TIMING); return;
            }
        }
        for (batch=0; batch<4U && process_index<1024U; batch++) {
            for (c=0; c<3; c++) {
                value[c]=((float)samples[process_index][c]-g_capOffsetUsed[c])*g_capGainUsed[c];
                input_sum[c]+=value[c];
                if (value[c]<g_capInputMin[c]) g_capInputMin[c]=value[c];
                if (value[c]>g_capInputMax[c]) g_capInputMax[c]=value[c];
            }
            rc=Cap_Push(value[0],value[1],value[2]);
            if (rc<0) { fail(CAP_ERR_MODEL); return; }
            process_index++;
        }
        if (process_index==1024U) {
            for (c=0; c<3; c++) g_capInputMean[c]=input_sum[c]/1024.0f;
            if (Cap_BeginFinish()!=0) { fail(CAP_ERR_MODEL); return; }
            g_capState=CAP_IMAGE;
        }
    } else {
        rc=Cap_FinishStep(output);
        if (rc<0) { fail(CAP_ERR_MODEL); return; }
        if (Cap_GetPhase()>=2U && g_capState==CAP_IMAGE) g_capState=CAP_CNN;
        if (rc==1) {
            for (c=0; c<4; c++) {
                if (!isfinite(output[c]) || !isfinite(output[c+4]) ||
                    output[c]<=0 || output[c]>10000 || output[c+4]<=0 || output[c+4]>10) {
                    fail(CAP_ERR_MODEL); return;
                }
            }
            for (c=0; c<4; c++) {
                g_capC_uF[c]=output[c]; g_capESR_Ohm[c]=output[c+4];
                g_capESR_mOhm[c]=1000.0f*output[c+4];
                if (output[c]<505.199f || output[c]>602.201f ||
                    output[c+4]<0.239999f || output[c+4]>0.320001f) g_capWarning|=4U;
            }
            g_capLatencyUs=(float)(uint32_t)(first_tick-CapPort_Timer())*us_per_tick;
            g_capSequence++; g_capState=CAP_DONE; g_capValid=1U;
        }
    }
    dt=(float)(uint32_t)(start-CapPort_Timer())*us_per_tick;
    if (old_state==CAP_WAVELET || old_state==CAP_IMAGE) g_capPreprocessUs+=dt;
    else g_capInferUs+=dt;
    if (dt>g_capTaskMaxUs) g_capTaskMaxUs=dt;
    if (g_capState==CAP_DONE) CapSCI_Publish();
}

void CapBackground_Task(uint16_t ready)
{
    uint32_t start=CapPort_Timer(), clock=CapPort_Clock();
    float us;
    CapMonitor_Task(ready);
    CapSCI_Task();
    if (clock) {
        us=(float)(uint32_t)(start-CapPort_Timer())*(1000000.0f/(float)clock);
        if (us>g_capBackgroundMaxUs) g_capBackgroundMaxUs=us;
    }
}
