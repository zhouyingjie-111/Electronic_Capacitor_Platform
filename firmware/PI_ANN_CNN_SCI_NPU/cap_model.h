#ifndef SCOPE_CAP_MODEL_H
#define SCOPE_CAP_MODEL_H
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
/* 固定网络尺寸：1024点输入、6x32x3图像、8维回归输出；尺寸需与权重配套。 */
#define CAP_WINDOW_SAMPLES 1024
#define CAP_IMAGE_FLOATS 576
#define CAP_OUTPUTS 8
                                                                    
                                                                  
                                                                     
                                                           
/* 开始新窗口前复位；所有接口共用内部静态工作区，只能由一个前台调用链使用。 */
void Cap_Reset(void);
                                                                          
                                                                          
/* 按100kHz连续输入Vc1(V)、ic(A)、io(A)；返回0收集中、1窗口齐全、-1错误。 */
int Cap_Push(float voltage, float capacitor_current, float load_current);
                                                                           
                                                                 
/* 同步完成预处理和推理；成功0、失败-1。输出为C1~C4(uF)、ESR1~ESR4(Ohm)。 */
int Cap_Finish(float output[CAP_OUTPUTS]);
                                                                           
/* 直接推理已归一化的HWC图像；共享工作区会被覆盖，不保留内部图像副本。 */
int Cap_PredictImage(const float image[CAP_IMAGE_FLOATS], float output[CAP_OUTPUTS]);
                                                                   
                                                                             
                                                                              
/* 观察路径：MakeImage后通过GetImage读取图像，并在后续推理前完成复制。 */
int Cap_MakeImage(void);
const float *Cap_GetImage(void);
                                                                        
                                                                         
                                                                       
                                                                
/* 分步路径：Push满1024点→BeginFinish一次→反复FinishStep至返回1；不可与同步路径混用。 */
int Cap_BeginFinish(void);
int Cap_FinishStep(float output[CAP_OUTPUTS]);
/* 阶段值：1成像、2第一层卷积、3第二层卷积、4输出、5结束；0尚未启动分步处理。 */
uint16_t Cap_GetPhase(void);
#ifdef __cplusplus
}
#endif
#endif
