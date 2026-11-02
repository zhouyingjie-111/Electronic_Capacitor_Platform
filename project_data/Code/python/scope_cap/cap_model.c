#include "cap_model.h"
#include "cap_weights.h"
#include <math.h>
#include <string.h>

/* 576 floats reused from accumulated energy to image to conv2 output.
 * 384 floats for conv1; 32 floats A5 voltage coefficients. */
static float work[576], conv1[384], a5_voltage[32];
static float pending[5][3], voltage_sum, voltage_reference;
static uint16_t pair_ready[5], count, invalid, finished;
static const float invsqrt2 = 0.7071067811865475244f;

static int finite_value(float v)
{
    return isfinite(v);
}

void Cap_Reset(void)
{
    memset(work, 0, sizeof(work));
    memset(pending, 0, sizeof(pending));
    memset(pair_ready, 0, sizeof(pair_ready));
    voltage_sum = 0.0f;
    voltage_reference = 0.0f;
    count = invalid = finished = 0U;
}

int Cap_Push(float v, float ic, float io)
{
    float a[3], next[3], detail;
    int level, c, bin;
    if (count >= CAP_WINDOW_SAMPLES || invalid || finished) return -1;
    if (!finite_value(v) || !finite_value(ic) || !finite_value(io)) {
        invalid = 1U;
        return -1;
    }
    if (count == 0U) voltage_reference = v;
    a[0] = v - voltage_reference;
    a[1] = ic; a[2] = io;
    voltage_sum += a[0];
    bin = count / 32U;
    count++;
    for (level = 0; level < 5; level++) {
        if (!pair_ready[level]) {
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
            if (iy<0 || iy>=ih) continue;
            for (kx=0; kx<3; kx++) {
                ix=x*2+kx; /* left padding zero for both convolutions */
                if (ix>=iw) continue;
                for (c=0; c<ci; c++) {
                    wi=((ky*3+kx)*ci+c)*co+o;
                    sum += input[(iy*iw+ix)*ci+c]*weights[wi];
                }
            }
        }
        output[(y*ow+x)*co+o] = sum>0.0f ? sum:0.0f;
    }
}

int Cap_PredictImage(const float *image, float *output)
{
    int i,c,o;
    float pooled[16], sum;
    if (!image || !output) return -1;
    for (i=0; i<576; i++) if (!finite_value(image[i])) return -1;
    convolution(image,6,32,3,cap_conv1_w,cap_conv1_b,8,3,16,0,conv1);
    /* Reuse work only AFTER conv1 is finished, including when image==work. */
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

/* Debug image storage is caller supplied through Cap_CopyImage BEFORE
 * inference; avoids keeping another 576-float array in production. */
int Cap_MakeImage(void)
{
    int row,bin,c,index,stat;
    float energy,value,dc,ncoeff;
    if (count != CAP_WINDOW_SAMPLES || invalid || finished) return -1;
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

const float *Cap_GetImage(void) { return work; }

int Cap_Finish(float *output)
{
    if (Cap_MakeImage() != 0) return -1;
    return Cap_PredictImage(work, output);
}
