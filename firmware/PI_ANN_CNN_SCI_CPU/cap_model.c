/* CPU侧电容CNN模型：1024点三通道数据经5层Haar能量、6x32x3成像后推理。 支持一次性接口与协作式接口，同一组静态工作区不可交错或并发使用。 */ #include "cap_model.h"
#include "cap_weights.h"
#include <math.h>
#include <string.h>

#if defined(__TI_COMPILER_VERSION__)
#pragma DATA_SECTION(work, ".cap_model")
#pragma DATA_SECTION(conv1, ".cap_model")
#pragma DATA_SECTION(a5_voltage, ".cap_model")
#endif
/* 共享内存：work依次保存能量、图像和第二层卷积输出；conv1保存第一层输出。 a5_voltage保存电压第五层近似系数，用于成像前去除窗口平均分量。 */ static float work[576], conv1[384], a5_voltage[32];
static float pending[5][3], voltage_sum, voltage_reference;
static uint16_t pair_ready[5], count, invalid, finished;
static uint16_t step_phase, step_index;
static const float invsqrt2 = 0.7071067811865475244f;

static int finite_value(float v)
{
    return isfinite(v);
}
/* 新窗口开始前清空能量、配对标志和阶段状态；静态工作区不支持多实例并发。 */
void Cap_Reset(void)
{
    memset(work, 0, sizeof(work));
    memset(pending, 0, sizeof(pending));
    memset(pair_ready, 0, sizeof(pair_ready));
    voltage_sum = 0.0f;
    voltage_reference = 0.0f;
    count = invalid = finished = 0U;
    step_phase = step_index = 0U;
}
/* 逐点输入物理量V、A、A；以首点电压建立参考，电流保留原有直流分量。 返回0表示继续收集，1表示已达到1024点，-1表示输入或窗口状态无效。 */
int Cap_Push(float v, float ic, float io)
{
    float a[3], next[3], detail;
    int level, c, bin;
    if (count >= CAP_WINDOW_SAMPLES || invalid || finished) return -1; /* 窗口已满、曾有非法输入或已完成成像时拒绝继续追加。 */
    if (!finite_value(v) || !finite_value(ic) || !finite_value(io)) { /* 任一物理量为NaN或无穷时将窗口标为无效。 */
        invalid = 1U;
        return -1;
    }
    if (count == 0U) voltage_reference = v;
    a[0] = v - voltage_reference;
    a[1] = ic; a[2] = io;
    voltage_sum += a[0];
    bin = count / 32U;
    count++;
    /* 每层先缓存一个系数，第二个到达时生成细节/近似系数；细节平方累加到对应时间箱。 */ for (level = 0; level < 5; level++) {
        if (!pair_ready[level]) { /* 本层尚无配对系数时先缓存，等待后续样本，不提前产生一对结果。 */
            for (c=0; c<3; c++) pending[level][c] = a[c];
            pair_ready[level] = 1U;
            return count == CAP_WINDOW_SAMPLES ? 1 : 0;
        }
        pair_ready[level] = 0U;
        for (c=0; c<3; c++) {
            detail = (pending[level][c] - a[c]) * invsqrt2;
            next[c] = (pending[level][c] + a[c]) * invsqrt2;
            work[(level*32+bin)*3+c] += detail*detail;
            a[c] = next[c];
        }
    }
    for (c=0; c<3; c++) work[(5*32+bin)*3+c] = a[c]*a[c];
    a5_voltage[bin] = a[0];
    return count == CAP_WINDOW_SAMPLES ? 1 : 0;
}
/* 3x3卷积、步长2、ReLU；按HWC读取输入，权重索引为[ky][kx][ci][co]。 */
static void convolution(const float *input, int ih, int iw, int ci,
                        const float *weights, const float *bias, int co,
                        int oh, int ow, int top, float *output)
{
    int y,x,o,ky,kx,c,iy,ix,wi;
    float sum;
    for (y=0; y<oh; y++) for (x=0; x<ow; x++) for (o=0; o<co; o++) {
        sum = bias[o];
        for (ky=0; ky<3; ky++) {
            iy=y*2+ky-top;
            if (iy<0 || iy>=ih) continue; /* 卷积越过上下边界的行不参与累加，相当于零填充。 */
            for (kx=0; kx<3; kx++) {
                ix=x*2+kx;                                              
                if (ix>=iw) continue; /* 卷积越过右边界的列不参与累加。 */
                for (c=0; c<ci; c++) {
                    wi=((ky*3+kx)*ci+c)*co+o;
                    sum += input[(iy*iw+ix)*ci+c]*weights[wi];
                }
            }
        }
        output[(y*ow+x)*co+o] = sum>0.0f ? sum:0.0f;
    }
}
/* 同步推理接口：两层卷积→16通道全局平均池化→8维线性回归及反标准化。 输出顺序为C1~C4(uF)、ESR1~ESR4(Ohm)；第一层完成后才复用work。 */
int Cap_PredictImage(const float *image, float *output)
{
    int i,c,o;
    float pooled[16], sum;
    if (!image || !output) return -1; /* 调用方必须提供有效输入图像和输出数组地址。 */
    for (i=0; i<576; i++) if (!finite_value(image[i])) return -1;
    convolution(image,6,32,3,cap_conv1_w,cap_conv1_b,8,3,16,0,conv1);
                                                                              
    convolution(conv1,3,16,8,cap_conv2_w,cap_conv2_b,16,2,8,1,work);
    for (c=0; c<16; c++) {
        sum=0.0f;
        for (i=0; i<16; i++) sum += work[i*16+c];
        pooled[c]=sum*(1.0f/16.0f);
    }
    for (o=0; o<8; o++) {
        sum=cap_dense_b[o];
        for (c=0; c<16; c++) sum += pooled[c]*cap_dense_w[c*8+o];
        output[o]=sum*cap_label_std[o]+cap_label_mean[o];
        if (!finite_value(output[o])) return -1;
    }
    return 0;
}

/* 将本窗口能量原地转换成归一化图像：电压A5去均值、能量归一化、log1p及[0,1]限幅。 本函数消耗当前窗口，成功后不可再次对同一窗口调用Finish。 */
int Cap_MakeImage(void)
{
    int row,bin,c,index,stat;
    float energy,value,dc,ncoeff;
    if (count != CAP_WINDOW_SAMPLES || invalid || finished) return -1; /* 只允许对完整且未消耗、未出错的窗口开始处理。 */
    dc=(voltage_sum*(1.0f/1024.0f))*5.6568542494923801952f;
    for (bin=0; bin<32; bin++) {
        value=a5_voltage[bin]-dc;
        work[(5*32+bin)*3]=value*value;
    }
    for (row=0; row<6; row++) {
        ncoeff = row<5 ? (float)(32U >> (row+1)):1.0f;
        for (bin=0; bin<32; bin++) for (c=0; c<3; c++) {
            index=(row*32+bin)*3+c;
            stat=row*3+c;
            energy=work[index]/ncoeff;
            value=(log1pf(energy/cap_energy_scale[stat])-cap_image_low[stat])
                  /cap_image_span[stat];
            if (!finite_value(value)) { invalid=1U; return -1; }
            work[index]=value<0.0f ? 0.0f:(value>1.0f ? 1.0f:value);
        }
    }
    finished=1U;
    return 0;
}
/* 只读观察共享图像；应在成像后、推理覆盖工作区前自行复制需要保留的数据。 */
const float *Cap_GetImage(void) { return work; }
/* 同步路径：一次成像后立即完成推理，不与协作式BeginFinish/FinishStep交错。 */
int Cap_Finish(float *output)
{
    if (Cap_MakeImage() != 0) return -1;
    return Cap_PredictImage(work, output);
}

uint16_t Cap_GetPhase(void) { return step_phase; }
/* 协作式入口：完整窗口到齐后初始化成像阶段，后续由主循环反复调用FinishStep。 */
int Cap_BeginFinish(void)
{
    int bin;
    float dc, v;
    if (count != CAP_WINDOW_SAMPLES || invalid || finished) return -1; /* 只允许对完整且未消耗、未出错的窗口开始处理。 */
    dc=(voltage_sum*(1.0f/1024.0f))*5.6568542494923801952f;
    for (bin=0; bin<32; bin++) {
        v=a5_voltage[bin]-dc;
        work[(5*32+bin)*3]=v*v;
    }
    finished=1U; step_phase=1U; step_index=0U;
    return 0;
}

/* 协作式步骤：phase1每次处理一个图像元素，phase2/3每次处理一个卷积输出。 phase4完成池化和回归，phase5表示结束；返回0继续、1完成、-1失败。 */
int Cap_FinishStep(float *output)
{
    int row,c,stat,oh,ow,ci,co,top,ih,iw,y,x,o,ky,kx,iy,ix,wi,i;
    float ncoeff,value,sum,pooled[16];
    const float *input,*weights,*bias;
    float *dest;
    if (!output || invalid || step_phase==0U) return -1; /* 输出指针无效、窗口异常或未启动分步处理时立即失败。 */
    if (step_phase==1U) { /* 成像阶段每次归一化一个元素，完成576个元素后进入卷积。 */
        row=step_index/(32U*3U); c=step_index%3U; stat=row*3+c;
        ncoeff=row<5 ? (float)(32U>>(row+1)):1.0f;
        value=(log1pf((work[step_index]/ncoeff)/cap_energy_scale[stat])-
               cap_image_low[stat])/cap_image_span[stat];
        if (!finite_value(value)) { invalid=1U; return -1; }
        work[step_index]=value<0.0f ? 0.0f:(value>1.0f ? 1.0f:value);
        if (++step_index==576U) { step_index=0U; step_phase=2U; }
        return 0;
    }
    if (step_phase==2U || step_phase==3U) { /* 两层卷积共用分步计算框架，每次产生一个输出标量。 */
        if (step_phase==2U) { /* 第一层读取图像写conv1；另一分支读取conv1并复用work。 */
            input=work; weights=cap_conv1_w; bias=cap_conv1_b; dest=conv1;
            ih=6; iw=32; ci=3; co=8; oh=3; ow=16; top=0;
        } else {
            input=conv1; weights=cap_conv2_w; bias=cap_conv2_b; dest=work;
            ih=3; iw=16; ci=8; co=16; oh=2; ow=8; top=1;
        }
        o=step_index%co; x=(step_index/co)%ow; y=step_index/(co*ow);
        sum=bias[o];
        for (ky=0; ky<3; ky++) {
            iy=y*2+ky-top;
            if (iy<0 || iy>=ih) continue; /* 卷积越过上下边界的行不参与累加，相当于零填充。 */
            for (kx=0; kx<3; kx++) {
                ix=x*2+kx;
                if (ix>=iw) continue; /* 卷积越过右边界的列不参与累加。 */
                for (c=0; c<ci; c++) {
                    wi=((ky*3+kx)*ci+c)*co+o;
                    sum+=input[(iy*iw+ix)*ci+c]*weights[wi];
                }
            }
        }
        dest[step_index]=sum>0.0f ? sum:0.0f;
        if (++step_index==oh*ow*co) { step_index=0U; step_phase++; }
        return 0;
    }
    if (step_phase==4U) { /* 两层卷积结束后执行池化、线性回归与输出反标准化。 */
        for (c=0; c<16; c++) {
            sum=0.0f;
            for (i=0; i<16; i++) sum+=work[i*16+c];
            pooled[c]=sum*(1.0f/16.0f);
        }
        for (o=0; o<8; o++) {
            sum=cap_dense_b[o];
            for (c=0; c<16; c++) sum+=pooled[c]*cap_dense_w[c*8+o];
            output[o]=sum*cap_label_std[o]+cap_label_mean[o];
            if (!finite_value(output[o])) { invalid=1U; return -1; }
        }
        step_phase=5U;
        return 1;
    }
    return 1;
}
