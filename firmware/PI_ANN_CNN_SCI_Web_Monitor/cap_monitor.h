#ifndef CAP_MONITOR_H
#define CAP_MONITOR_H
#include <stdint.h>
enum { CAP_IDLE=0, CAP_CAPTURE=1, CAP_WAVELET=2, CAP_IMAGE=3,
       CAP_CNN=4, CAP_DONE=5, CAP_ERROR=6 };
enum { CAP_ERR_ADC_OVERFLOW=1, CAP_ERR_ADC_RAIL=2, CAP_ERR_TIMING=4,
       CAP_ERR_MODEL=8, CAP_ERR_CONFIG=16, CAP_ERR_CANCEL=32,
       CAP_ERR_NOT_READY=64 };
/* CCS controls: set Request=1 after the converter settles. Default single-shot.
 * Result arrays follow physical board slots P1/P2/P3/P4, never sort by value. */
extern volatile uint16_t g_capEnable, g_capRequest, g_capState, g_capValid;
extern volatile uint16_t g_capFault, g_capWarning, g_capSampleCount;
extern volatile uint32_t g_capSequence, g_capErrorCount, g_capAdcOverflowCount;
extern volatile float g_capC_uF[4], g_capESR_Ohm[4], g_capESR_mOhm[4];
extern volatile float g_capSampleRateHz, g_capMaxSampleGapUs;
extern volatile float g_capPreprocessUs, g_capInferUs, g_capTaskMaxUs;
extern volatile float g_capBackgroundMaxUs;
extern volatile float g_capLatencyUs, g_capAdcIsrMaxUs;
extern volatile float g_capInputMin[3], g_capInputMax[3], g_capInputMean[3];
extern volatile float g_capOffsetUsed[3], g_capGainUsed[3];
void CapMonitor_Init(void);
void CapMonitor_Task(uint16_t converter_started);
/* Main-loop wrapper also measures framing/transmit/arming overhead. */
void CapBackground_Task(uint16_t converter_started);
/* Call once per ADC ISR after raw readings, before clearing ADCINT flags.
 * raw_voltage=Vs1 field (physical Vc1), raw_ic=ic, raw_io=io.
 * entry_timer is the EXISTING Timer0 timestamp at ISR entry. */
void CapMonitor_OnAdc(uint16_t raw_voltage, uint16_t raw_ic, uint16_t raw_io,
                      uint32_t entry_timer, uint16_t overflow);
#endif
