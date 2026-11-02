#ifndef NN_CPU_TEST_H_
#define NN_CPU_TEST_H_

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

extern volatile float g_nnInput[5];
extern volatile float g_nnOutput[1];
extern volatile int32_t g_nnReturnCode;
                                                                       
extern volatile uint16_t g_nnTestState;

                                                                          
extern volatile uint32_t g_nnTimeCycles;
extern volatile float g_nnTimeUs;
extern volatile float g_nnTimeFirstUs;
extern volatile float g_nnTimeMinUs;
extern volatile float g_nnTimeMaxUs;
extern volatile uint32_t g_nnSampleCount;
                                                                           
extern volatile uint16_t g_nnBenchmarkDone;
extern volatile uint32_t g_nnTimerClockHz;
extern volatile uint32_t g_nnReadOverheadCycles;
extern volatile float g_nnReadOverheadUs;

                                                                              
void NN_initTiming(void);
extern volatile uint16_t g_nnNpuFaultLatched;
extern volatile uint16_t g_nnBootStage;
extern volatile uint32_t g_nnNpuLastIrqDelta;
                                                                         
void NN_runOnce(void);
                                                                          
void NN_runBenchmark(uint16_t runCount);

#ifdef __cplusplus
}
#endif
#endif
