#include "driverlib.h"
#include "device.h"
#include "nn_cpu_test.h"
#include "tvmgen_default.h"
#include "ann_npu_adapter.h"
#include "nn_npu_platform.h"
#include <math.h>

#if !defined(TVMGEN_DEFAULT_TI_NPU) || defined(TVMGEN_DEFAULT_TI_NPU_SOFT)
#error The matching hardware NPU model library and header are required.
#endif

                                                                
#define NN_TIMER_BASE CPUTIMER0_BASE
#define NN_TIMER_PERIOD 0xFFFFFFFFUL
#define NN_CLOCK_ERROR (-1000L)

                                                                   
                                                                          
#define NN_NPU_TIMEOUT_US 1000UL
volatile uint16_t g_nnNpuFaultLatched = 0U;
volatile uint16_t g_nnBootStage = 0U;
volatile uint32_t g_nnNpuLastIrqDelta = 0UL;
volatile float g_nnInput[5] = {0.0f, 0.0f, 0.0f, 0.0f, 0.0f};
volatile float g_nnOutput[1] = {0.0f};
volatile int32_t g_nnReturnCode = -1;
volatile uint16_t g_nnTestState = 0U;

volatile uint32_t g_nnTimeCycles = 0UL;
volatile float g_nnTimeUs = 0.0f;
volatile float g_nnTimeFirstUs = 0.0f;
volatile float g_nnTimeMinUs = 0.0f;
volatile float g_nnTimeMaxUs = 0.0f;
volatile uint32_t g_nnSampleCount = 0UL;
volatile uint16_t g_nnBenchmarkDone = 0U;
volatile uint32_t g_nnTimerClockHz = 0UL;
volatile uint32_t g_nnReadOverheadCycles = 0UL;
volatile float g_nnReadOverheadUs = 0.0f;

static float nnInputBuffer[5];
static float nnMicrosecondsPerTick;
static uint16_t nnTimerInitialized = 0U;
static uint32_t nnMinCycles;
static uint32_t nnMaxCycles;

                                                                    
                                                                      
   
#if defined(__TI_COMPILER_VERSION__)
#pragma FUNC_CANNOT_INLINE(NN_readTimer)
#elif defined(__GNUC__)
__attribute__((noinline))
#endif
static uint32_t NN_readTimer(void)
{
    return CPUTimer_getTimerCount(NN_TIMER_BASE);
}

void NN_initTiming(void)
{
    uint16_t i;
    uint32_t start;
    uint32_t end;
    uint32_t overhead;

    g_nnBootStage = 1U;
    nnTimerInitialized = 0U;
    g_nnTimeCycles = 0UL;
    g_nnTimeUs = 0.0f;
    g_nnTimeFirstUs = 0.0f;
    g_nnTimeMinUs = 0.0f;
    g_nnTimeMaxUs = 0.0f;
    g_nnSampleCount = 0UL;
    g_nnBenchmarkDone = 0U;
    g_nnTestState = 0U;
    g_nnReturnCode = -1;
    g_nnReadOverheadCycles = 0UL;
    g_nnReadOverheadUs = 0.0f;
    nnMinCycles = 0UL;
    nnMaxCycles = 0UL;

                                                                           
                                                                      
                                                                    
       
    g_nnTimerClockHz = SysCtl_getClock(DEVICE_OSCSRC_FREQ);
    if(g_nnTimerClockHz == 0UL)
    {
        g_nnReturnCode = NN_CLOCK_ERROR;
        g_nnTestState = 3U;
        return;
    }
    nnMicrosecondsPerTick = 1000000.0f / (float)g_nnTimerClockHz;

    SysCtl_enablePeripheral(SYSCTL_PERIPH_CLK_TIMER0);
    CPUTimer_stopTimer(NN_TIMER_BASE);
    CPUTimer_disableInterrupt(NN_TIMER_BASE);
    CPUTimer_setPeriod(NN_TIMER_BASE, NN_TIMER_PERIOD);
    CPUTimer_setPreScaler(NN_TIMER_BASE, 0U);
    CPUTimer_setEmulationMode(
        NN_TIMER_BASE, CPUTIMER_EMULATIONMODE_STOPAFTERNEXTDECREMENT);
    CPUTimer_clearOverflowFlag(NN_TIMER_BASE);
    CPUTimer_startTimer(NN_TIMER_BASE);

                                                                             
                                                                           
       
    g_nnReadOverheadCycles = NN_TIMER_PERIOD;
    for(i = 0U; i < 8U; i++)
    {
        start = NN_readTimer();
        end = NN_readTimer();
        overhead = (uint32_t)(start - end);
        if(overhead < g_nnReadOverheadCycles)
        {
            g_nnReadOverheadCycles = overhead;
        }
    }
    g_nnReadOverheadUs = (float)g_nnReadOverheadCycles *
                         nnMicrosecondsPerTick;
    nnTimerInitialized = 1U;
    g_nnBootStage=2U;
                                                                
    g_nnBootStage=3U;
}

void NN_runOnce(void)
{
    uint16_t i;
    int32_t status;
    uint32_t start;
    uint32_t end;
    uint32_t elapsed;
    float elapsedUs;
    uint32_t irqBefore;
    uint32_t timeoutTicks;
    float dtGiven = 0.0f;

    if(nnTimerInitialized == 0U)
    {
        NN_initTiming();
        if(nnTimerInitialized == 0U)
        {
            return;
        }
    }

    if (g_nnNpuFaultLatched != 0U)
    {
        g_nnReturnCode = -1101L;
        g_nnTestState = 3U;
        return;
    }
    if (g_nnNpuInitOK == 0U)
    {
                                                                              
                                                                         
                                                                  
        if (NN_NPU_initHardware() == 0U)
        {
            g_nnReturnCode=-1100L;
            g_nnTestState=3U;
            g_nnNpuFaultLatched=1U;
            g_nnBootStage=0x8001U;
            return;
        }
    }
    g_nnTestState = 1U;
    g_nnReturnCode = -1L;
    for(i = 0U; i < 5U; i++) nnInputBuffer[i] = g_nnInput[i];
    irqBefore = g_nnNpuIrqCount;
    timeoutTicks = (g_nnTimerClockHz / 1000000UL) * NN_NPU_TIMEOUT_US;

                                                                          
                                                                    
                                                                                    
    start = NN_readTimer();
    status = (int32_t)ann_npu_start(nnInputBuffer);
    if (status != 0L)
    {
        g_nnReturnCode = -1200L;
        g_nnTestState = 3U;
        g_nnNpuFaultLatched = 1U;
        NN_NPU_stopOnFault();
        return;
    }
    do
    {
                                                                         
                                                                              
        if ((uint32_t)(start - NN_readTimer()) >= timeoutTicks)
        {
            status = -1103L;
            break;
        }
        NN_NPU_service();
        status = (int32_t)ann_npu_poll(&dtGiven);
    } while (status == 0L);
    end = NN_readTimer();
    g_nnNpuLastIrqDelta = (uint32_t)(g_nnNpuIrqCount - irqBefore);

                                                                       
    elapsed = (uint32_t)(start - end);
    elapsedUs = (float)elapsed * nnMicrosecondsPerTick;
    g_nnTimeCycles = elapsed;
    g_nnTimeUs = elapsedUs;
    if (status != 1L || g_nnNpuLastIrqDelta != 3UL || !isfinite(dtGiven))
    {
        g_nnReturnCode = (status == -1103L) ? -1103L : -1104L;
        g_nnTestState = 3U;
        g_nnNpuFaultLatched = 1U;
        NN_NPU_stopOnFault();
        return;
    }
    g_nnReturnCode = 0L;
    g_nnOutput[0] = dtGiven;

    if(g_nnSampleCount == 0UL)
    {
        nnMinCycles = elapsed;
        nnMaxCycles = elapsed;
        g_nnTimeFirstUs = elapsedUs;
    }
    else
    {
        if(elapsed < nnMinCycles)
        {
            nnMinCycles = elapsed;
        }
        if(elapsed > nnMaxCycles)
        {
            nnMaxCycles = elapsed;
        }
    }
    g_nnTimeMinUs = (float)nnMinCycles * nnMicrosecondsPerTick;
    g_nnTimeMaxUs = (float)nnMaxCycles * nnMicrosecondsPerTick;
    g_nnSampleCount++;
    g_nnTestState = 2U;
}

void NN_runBenchmark(uint16_t runCount)
{
    uint16_t i;
    NN_initTiming();
    if(nnTimerInitialized == 0U)
    {
        g_nnBenchmarkDone = 1U;
        return;
    }
    for(i = 0U; i < runCount; i++)
    {
        NN_runOnce();
        if(g_nnTestState != 2U)
        {
            break;
        }
    }
    g_nnBenchmarkDone = 1U;
}
