#include "driverlib.h"
#include "device.h"
#include "nn_cpu_test.h"
#include "tvmgen_default.h"

#if !defined(TVMGEN_DEFAULT_TI_NPU_SOFT) || defined(TVMGEN_DEFAULT_TI_NPU)
#error This timing test requires the matching CPU model library and header.
#endif

/* Confirmed unused in the supplied main and SysConfig files. */
#define NN_TIMER_BASE CPUTIMER0_BASE
#define NN_TIMER_PERIOD 0xFFFFFFFFUL
#define NN_CLOCK_ERROR (-1000L)

/* Zero input was already compared against PC ONNX inference by the user.
 * Use valid training-domain samples for subsequent tests. No extra scaling
 * or channel mapping is guessed here; input/output units are unknown.
 */
volatile float g_nnInput[5] = {0.0f, 0.0f, 0.0f, 0.0f, 0.0f};
volatile float g_nnOutput[2] = {0.0f, 0.0f};
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
static float nnOutputBuffer[2];
static float nnMicrosecondsPerTick;
static uint16_t nnTimerInitialized = 0U;
static uint32_t nnMinCycles;
static uint32_t nnMaxCycles;

/* TI profiling guidance uses a non-inlined timestamp reader to keep
 * optimization from moving measured work across timestamp boundaries.
 */
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

    /* Uses actual clock configuration and the board oscillator definition.
     * The oscillator value in device.h must match the physical board.
     * Timer0 clock is SYSCLK, with timer prescaler set to /1 below.
     */
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

    /* Approximate minimum empty-bracket overhead. It is reported separately,
     * never subtracted from model times to avoid optimistic timing claims.
     */
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
}

void NN_runOnce(void)
{
    uint16_t i;
    int32_t status;
    uint32_t start;
    uint32_t end;
    uint32_t elapsed;
    float elapsedUs;
    struct tvmgen_default_inputs inputs;
    struct tvmgen_default_outputs outputs;

    if(nnTimerInitialized == 0U)
    {
        NN_initTiming();
        if(nnTimerInitialized == 0U)
        {
            return;
        }
    }

    g_nnTestState = 1U;
    g_nnReturnCode = -1;
    for(i = 0U; i < 5U; i++)
    {
        nnInputBuffer[i] = g_nnInput[i];
    }
    for(i = 0U; i < 2U; i++)
    {
        nnOutputBuffer[i] = 0.0f;
        g_nnOutput[i] = 0.0f;
    }
    inputs.raw_input = (void *)nnInputBuffer;
    outputs.output = (void *)nnOutputBuffer;

    /* MEASURED REGION: synchronous model call only, no SCI or conversion.
     * Leave the existing interrupt state untouched. Do not single-step or
     * halt inside this bracket. Each interval must be < one full timer wrap
     * (about 28.63 seconds at 150 MHz).
     */
    start = NN_readTimer();
    status = tvmgen_default_run(&inputs, &outputs);
    end = NN_readTimer();

    /* Timer counts DOWN. Unsigned subtraction handles one rollover. */
    elapsed = (uint32_t)(start - end);
    elapsedUs = (float)elapsed * nnMicrosecondsPerTick;
    g_nnTimeCycles = elapsed;
    g_nnTimeUs = elapsedUs;
    g_nnReturnCode = status;

    if(status != 0)
    {
        g_nnTestState = 3U;
        return;
    }

    for(i = 0U; i < 2U; i++)
    {
        g_nnOutput[i] = nnOutputBuffer[i];
    }

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
