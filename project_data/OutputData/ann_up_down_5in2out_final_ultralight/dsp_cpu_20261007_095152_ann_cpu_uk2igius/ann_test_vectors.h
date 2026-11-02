#ifndef ANN_TEST_VECTORS_H
#define ANN_TEST_VECTORS_H

#define ANN_TEST_VECTOR_COUNT (8U)

/* Columns: P_peak, T_rise_fall, VC1, VG1, direction_state */
static const float ANN_TEST_INPUTS[ANN_TEST_VECTOR_COUNT][5] = {
    { 18.0f, 2.99999992e-05f, 48.6308365f, 222.332642f, 1.0f },
    { 18.0f, 2.99999992e-05f, 71.8756409f, 197.463577f, 0.0f },
    { 18.0f, 3.9999999e-05f, 48.5862961f, 222.394531f, 1.0f },
    { 18.0f, 3.9999999e-05f, 71.8507538f, 197.499237f, 0.0f },
    { 19.0f, 1.99999995e-05f, 47.9296875f, 223.077621f, 1.0f },
    { 19.0f, 1.99999995e-05f, 72.4897079f, 196.817978f, 0.0f },
    { 19.0f, 7.00000019e-05f, 47.9001312f, 223.106888f, 1.0f },
    { 19.0f, 7.00000019e-05f, 72.3744354f, 196.915741f, 0.0f }
};

/* Columns: DTgiven, DSsample. Values are PyTorch float32 predictions. */
static const float ANN_TEST_EXPECTED[ANN_TEST_VECTOR_COUNT][2] = {
    { 0.564016402f, 0.292870969f },
    { 0.0863017291f, 0.0793720037f },
    { 0.416061848f, 0.391247749f },
    { 0.12654148f, 0.106036395f },
    { 0.723800778f, 0.195885763f },
    { 0.0364187658f, 0.0463176817f },
    { 0.369750589f, 0.373448849f },
    { 0.209835976f, 0.210627526f }
};

#endif /* ANN_TEST_VECTORS_H */
