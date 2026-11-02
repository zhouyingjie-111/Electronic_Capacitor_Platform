#ifndef NN_CPU_TEST_H_
#define NN_CPU_TEST_H_

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

extern volatile float g_nnInput[5];
extern volatile float g_nnOutput[2];
extern volatile int32_t g_nnReturnCode;
/* 0: not run; 1: executing; 2: returned success; 3: returned error. */
extern volatile uint16_t g_nnTestState;

/* Raw bracket time, including timestamp overhead; NOT the complete loop. */
extern volatile uint32_t g_nnTimeCycles;
extern volatile float g_nnTimeUs;
extern volatile float g_nnTimeFirstUs;
extern volatile float g_nnTimeMinUs;
extern volatile float g_nnTimeMaxUs;
extern volatile uint32_t g_nnSampleCount;
/* 1 means the benchmark returned (check return code/count for success). */
extern volatile uint16_t g_nnBenchmarkDone;
extern volatile uint32_t g_nnTimerClockHz;
extern volatile uint32_t g_nnReadOverheadCycles;
extern volatile float g_nnReadOverheadUs;

/* Reserves CPU Timer0, does not enable its interrupt. Resets timing stats. */
void NN_initTiming(void);
/* Synchronous, foreground-only, non-reentrant. */
void NN_runOnce(void);
/* Initializes timing and runs up to runCount calls, stopping on error. */
void NN_runBenchmark(uint16_t runCount);

#ifdef __cplusplus
}
#endif
#endif
