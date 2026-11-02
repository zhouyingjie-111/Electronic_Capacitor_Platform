/*****************************************************************************/
/* Copyright (c) 2025 Texas Instruments Incorporated                         */
/* http://www.ti.com/                                                        */
/*                                                                           */
/*  Redistribution and  use in source  and binary forms, with  or without    */
/*  modification,  are permitted provided  that the  following conditions    */
/*  are met:                                                                 */
/*                                                                           */
/*     Redistributions  of source  code must  retain the  above copyright    */
/*     notice, this list of conditions and the following disclaimer.         */
/*                                                                           */
/*     Redistributions in binary form  must reproduce the above copyright    */
/*     notice, this  list of conditions  and the following  disclaimer in    */
/*     the  documentation  and/or   other  materials  provided  with  the    */
/*     distribution.                                                         */
/*                                                                           */
/*     Neither the  name of Texas Instruments Incorporated  nor the names    */
/*     of its  contributors may  be used to  endorse or  promote products    */
/*     derived  from   this  software  without   specific  prior  written    */
/*     permission.                                                           */
/*                                                                           */
/*  THIS SOFTWARE  IS PROVIDED BY THE COPYRIGHT  HOLDERS AND CONTRIBUTORS    */
/*  "AS IS"  AND ANY  EXPRESS OR IMPLIED  WARRANTIES, INCLUDING,  BUT NOT    */
/*  LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR    */
/*  A PARTICULAR PURPOSE ARE DISCLAIMED.  IN NO EVENT SHALL THE COPYRIGHT    */
/*  OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,    */
/*  SPECIAL,  EXEMPLARY,  OR CONSEQUENTIAL  DAMAGES  (INCLUDING, BUT  NOT    */
/*  LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,    */
/*  DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY    */
/*  THEORY OF  LIABILITY, WHETHER IN CONTRACT, STRICT  LIABILITY, OR TORT    */
/*  (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE    */
/*  OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.     */
/*                                                                           */
/*****************************************************************************/
// tvm target: c -keys=arm_cpu,cpu -mcpu=cortex-m0plus
#include "tvm/runtime/contrib/tvm_tinie_c28.h"
#define TVM_EXPORTS
#include "tvm/runtime/c_runtime_api.h"
#include "tvm/runtime/c_backend_api.h"
#include <math.h>
#include <stdbool.h>
#ifdef __cplusplus
extern "C"
#endif
TVM_DLL int32_t tvmgen_default_fused_nn_contrib_dense_pack_add(float* p0, float* T_add, uint8_t* global_const_workspace_8_var, uint8_t* global_workspace_9_var);
#ifdef __cplusplus
extern "C"
#endif
TVM_DLL int32_t tvmgen_default_fused_nn_contrib_dense_pack_add_nn_relu(float* p0, float* T_relu, uint8_t* global_const_workspace_4_var, uint8_t* global_workspace_5_var);
#ifdef __cplusplus
extern "C"
#endif
TVM_DLL int32_t tvmgen_default_fused_nn_contrib_dense_pack_add_nn_relu_1(float* p0, float* T_relu, uint8_t* global_const_workspace_6_var, uint8_t* global_workspace_7_var);
#ifdef __cplusplus
extern "C"
#endif
TVM_DLL int32_t tvmgen_default_fused_strided_slice_strided_slice_greater_equal_cast_concatenate(float* p0, float* T_concat, uint8_t* global_const_workspace_2_var, uint8_t* global_workspace_3_var);
#ifdef __cplusplus
extern "C"
#endif
TVM_DLL int32_t tvmgen_default___tvm_main__(float* raw_input_buffer_var, float* output_buffer_var, uint8_t* global_const_workspace_0_var, uint8_t* global_workspace_1_var);
#ifdef __cplusplus
extern "C"
#endif
TVM_DLL int32_t tvmgen_default_fused_nn_contrib_dense_pack_add(float* p0, float* T_add, uint8_t* global_const_workspace_8_var, uint8_t* global_workspace_9_var) {
  void* fused_nn_contrib_dense_pack_constant_2_let = (&(global_const_workspace_8_var[192]));
  void* fused_constant_2_let = (&(global_const_workspace_8_var[160]));
  void* compute_global_let = (&(global_workspace_9_var[16]));
  for (int32_t x_c_init = 0; x_c_init < 2; ++x_c_init) {
    ((float*)compute_global_let)[x_c_init] = 0.000000e+00f;
  }
  for (int32_t k_outer = 0; k_outer < 4; ++k_outer) {
    for (int32_t x_c = 0; x_c < 2; ++x_c) {
      ((float*)compute_global_let)[x_c] = (((float*)compute_global_let)[x_c] + (p0[k_outer] * ((float*)fused_constant_2_let)[((k_outer * 2) + x_c)]));
    }
  }
  for (int32_t ax1_inner_inner = 0; ax1_inner_inner < 2; ++ax1_inner_inner) {
    T_add[ax1_inner_inner] = (((float*)compute_global_let)[ax1_inner_inner] + ((float*)fused_nn_contrib_dense_pack_constant_2_let)[ax1_inner_inner]);
  }
  return 0;
}

#ifdef __cplusplus
extern "C"
#endif
TVM_DLL int32_t tvmgen_default_fused_nn_contrib_dense_pack_add_nn_relu(float* p0, float* T_relu, uint8_t* global_const_workspace_4_var, uint8_t* global_workspace_5_var) {
  void* fused_nn_contrib_dense_pack_constant_let = (&(global_const_workspace_4_var[144]));
  void* fused_constant_let = (&(global_const_workspace_4_var[0]));
  void* compute_global_let = (&(global_workspace_5_var[16]));
  for (int32_t x_c_init = 0; x_c_init < 8; ++x_c_init) {
    ((float*)compute_global_let)[x_c_init] = 0.000000e+00f;
  }
  for (int32_t k_outer = 0; k_outer < 5; ++k_outer) {
    for (int32_t x_c = 0; x_c < 8; ++x_c) {
      ((float*)compute_global_let)[x_c] = (((float*)compute_global_let)[x_c] + (p0[k_outer] * ((float*)fused_constant_let)[((k_outer * 8) + x_c)]));
    }
  }
  for (int32_t ax1_inner_inner = 0; ax1_inner_inner < 8; ++ax1_inner_inner) {
    float v_ = ((float*)compute_global_let)[ax1_inner_inner] + ((float*)fused_nn_contrib_dense_pack_constant_let)[ax1_inner_inner];
    T_relu[ax1_inner_inner] = ((v_) > (0.000000e+00f) ? (v_) : (0.000000e+00f));
  }
  return 0;
}

#ifdef __cplusplus
extern "C"
#endif
TVM_DLL int32_t tvmgen_default_fused_nn_contrib_dense_pack_add_nn_relu_1(float* p0, float* T_relu, uint8_t* global_const_workspace_6_var, uint8_t* global_workspace_7_var) {
  void* fused_nn_contrib_dense_pack_constant_1_let = (&(global_const_workspace_6_var[176]));
  void* fused_constant_1_let = (&(global_const_workspace_6_var[80]));
  void* compute_global_let = (&(global_workspace_7_var[16]));
  for (int32_t x_c_init = 0; x_c_init < 4; ++x_c_init) {
    ((float*)compute_global_let)[x_c_init] = 0.000000e+00f;
  }
  for (int32_t k_outer = 0; k_outer < 8; ++k_outer) {
    for (int32_t x_c = 0; x_c < 4; ++x_c) {
      ((float*)compute_global_let)[x_c] = (((float*)compute_global_let)[x_c] + (p0[k_outer] * ((float*)fused_constant_1_let)[((k_outer * 4) + x_c)]));
    }
  }
  for (int32_t ax1_inner_inner = 0; ax1_inner_inner < 4; ++ax1_inner_inner) {
    float v_ = ((float*)compute_global_let)[ax1_inner_inner] + ((float*)fused_nn_contrib_dense_pack_constant_1_let)[ax1_inner_inner];
    T_relu[ax1_inner_inner] = ((v_) > (0.000000e+00f) ? (v_) : (0.000000e+00f));
  }
  return 0;
}

#ifdef __cplusplus
extern "C"
#endif
TVM_DLL int32_t tvmgen_default_fused_strided_slice_strided_slice_greater_equal_cast_concatenate(float* p0, float* T_concat, uint8_t* global_const_workspace_2_var, uint8_t* global_workspace_3_var) {
  for (int32_t ax1 = 0; ax1 < 5; ++ax1) {
    float condval;
    if ((ax1 == 4)) {
      condval = ((float)(5.000000e-01f <= p0[ax1]));
    } else {
      condval = p0[ax1];
    }
    T_concat[ax1] = condval;
  }
  return 0;
}

#ifdef __cplusplus
extern "C"
#endif
TVM_DLL int32_t tvmgen_default___tvm_main__(float* raw_input_buffer_var, float* output_buffer_var, uint8_t* global_const_workspace_0_var, uint8_t* global_workspace_1_var) {
  void* sid_2_let = (&(global_workspace_1_var[0]));
  void* sid_1_let = (&(global_workspace_1_var[0]));
  void* sid_3_let = (&(global_workspace_1_var[0]));
  tvmgen_default_fused_strided_slice_strided_slice_greater_equal_cast_concatenate(raw_input_buffer_var, sid_1_let, global_const_workspace_0_var, global_workspace_1_var);
  tvmgen_default_fused_nn_contrib_dense_pack_add_nn_relu(sid_1_let, sid_2_let, global_const_workspace_0_var, global_workspace_1_var);
  tvmgen_default_fused_nn_contrib_dense_pack_add_nn_relu_1(sid_2_let, sid_3_let, global_const_workspace_0_var, global_workspace_1_var);
  tvmgen_default_fused_nn_contrib_dense_pack_add(sid_3_let, output_buffer_var, global_const_workspace_0_var, global_workspace_1_var);
  return 0;
}

