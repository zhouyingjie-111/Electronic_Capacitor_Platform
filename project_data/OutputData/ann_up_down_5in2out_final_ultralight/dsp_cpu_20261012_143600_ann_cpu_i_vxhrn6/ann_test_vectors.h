#ifndef ANN_TEST_VECTORS_H
#define ANN_TEST_VECTORS_H

#define ANN_TEST_VECTOR_COUNT (8U)

/* Columns: P_peak, T_rise_fall, VC1, VG1, direction_state */
static const float ANN_TEST_INPUTS[ANN_TEST_VECTOR_COUNT][5] = {
    { 18.0f, 2.99999992e-05f, 67.7532425f, 202.398102f, 0.0f },
    { 18.0f, 2.99999992e-05f, 67.7532425f, 202.398102f, 1.0f },
    { 18.0f, 3.9999999e-05f, 67.7185287f, 202.446884f, 0.0f },
    { 18.0f, 3.9999999e-05f, 67.7185287f, 202.446884f, 1.0f },
    { 19.0f, 1.99999995e-05f, 67.7097015f, 202.4478f, 0.0f },
    { 19.0f, 1.99999995e-05f, 67.7097015f, 202.4478f, 1.0f },
    { 19.0f, 7.00000019e-05f, 67.6372833f, 202.511322f, 0.0f },
    { 19.0f, 7.00000019e-05f, 67.6372833f, 202.511322f, 1.0f }
};

/* Columns: DTgiven, DSsample. Values are PyTorch float32 predictions. */
static const float ANN_TEST_EXPECTED[ANN_TEST_VECTOR_COUNT][2] = {
    { 0.0441325903f, 0.053503722f },
    { 0.525751472f, 0.330172211f },
    { 0.0855898857f, 0.0957491547f },
    { 0.47137022f, 0.382252932f },
    { 0.0515408218f, 0.00401504338f },
    { 0.751827478f, 0.17158252f },
    { 0.22411938f, 0.209062278f },
    { 0.367848545f, 0.371090353f }
};

#endif /* ANN_TEST_VECTORS_H */
