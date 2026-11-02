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
    { 0.528565586f, 0.349154502f },
    { 0.067491129f, 0.0481692553f },
    { 0.448176324f, 0.407622039f },
    { 0.101142988f, 0.0865494013f },
    { 0.757444859f, 0.185979515f },
    { 0.0623310804f, 0.0476642698f },
    { 0.377601385f, 0.388669014f },
    { 0.211626977f, 0.218792155f }
};

#endif /* ANN_TEST_VECTORS_H */
