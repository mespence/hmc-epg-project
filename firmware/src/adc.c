#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
LOG_MODULE_REGISTER(adc_dma, LOG_LEVEL_INF);
#include <nrfx_saadc.h>
#include <nrfx_timer.h>
#include <helpers/nrfx_gppi.h>
#include <SEGGER_RTT.h>
#if defined(DPPI_PRESENT)
#include <nrfx_dppi.h>
#else
#include <nrfx_ppi.h>
#endif

#include "../include/adc.h"
#include <zephyr/sys/util.h>


/* -----------------------------
 * User configuration
 * ----------------------------- */
#define ADC_SAMPLE_RATE_HZ      3000
#define SAADC_BUFFER_SIZE       3000      // per block
#define TIMER_INSTANCE_NUMBER   1         // TIMER2 on nRF5340

/* -----------------------------
 * Derived settings
 * ----------------------------- */
#define SAADC_INTERVAL_US (1000000 / ADC_SAMPLE_RATE_HZ)

/* ----------------------------- */
static const nrfx_timer_t timer_instance = NRFX_TIMER_INSTANCE(TIMER_INSTANCE_NUMBER);
static int16_t buf0[SAADC_BUFFER_SIZE];
static int16_t buf1[SAADC_BUFFER_SIZE];
static uint8_t which = 0;
static volatile bool saadc_calibrated = false;






// NEW

#include "../include/bluetooth.h"   // we’ll call a BLE helper from here

#define SAMPLES_PER_NOTIFY  40     // or include from a common header

/* Work item for sending one full SAADC block over BLE */
static struct k_work saadc_ble_work;

/* Staging buffer for last completed SAADC block */
static int16_t g_ble_buf[SAADC_BUFFER_SIZE];
static size_t  g_ble_len = 0;



static void saadc_ble_work_handler(struct k_work *work)
{
    // For now just stub; you can fill in with bt_gatt_notify later
    // using g_ble_buf[0..g_ble_len)
        /* Send g_ble_buf[0 .. g_ble_len) as binary int16_t samples over BLE
     * in chunks of SAMPLES_PER_NOTIFY samples.
     */
    size_t idx = 0;

    while (idx < g_ble_len) {
        size_t remaining = g_ble_len - idx;
        size_t chunk_samples = MIN(remaining, (size_t)SAMPLES_PER_NOTIFY);

        /* 2 bytes per sample */
        uint8_t payload[2 * SAMPLES_PER_NOTIFY];

        for (size_t i = 0; i < chunk_samples; i++) {
            int16_t sample = g_ble_buf[idx + i];

            /* Pack as little-endian int16_t */
            payload[2 * i + 0] = (uint8_t)(sample & 0xFF);
            payload[2 * i + 1] = (uint8_t)((sample >> 8) & 0xFF);
        }

        size_t payload_len = 2 * chunk_samples;

        /* Call a BLE helper that does bt_gatt_notify() on a binary characteristic */
        ble_send_adc_chunk(payload, payload_len);

        idx += chunk_samples;
    }
}



/* STEP 4.2 - Declare the buffers for the SAADC */
static int16_t saadc_sample_buffer[2][SAADC_BUFFER_SIZE];


/* STEP 4.3 - Declare variable used to keep track of which buffer was last assigned to the SAADC driver */
static uint32_t saadc_current_buffer = 0;

/* STEP 4.6 - Declare the struct to hold the configuration for the SAADC channel used to sample the battery voltage */
#define SAADC_INPUT_PIN NRF_SAADC_INPUT_AIN0
static nrfx_saadc_channel_t channel = NRFX_SAADC_DEFAULT_CHANNEL_SE(SAADC_INPUT_PIN, 0);


/* -----------------------------
 * Configure TIMER
 * ----------------------------- */
static void configure_timer(void)
{
    nrfx_err_t err;

    nrfx_timer_config_t timer_config = NRFX_TIMER_DEFAULT_CONFIG(1000000);
    err = nrfx_timer_init(&timer_instance, &timer_config, NULL);
    if (err != NRFX_SUCCESS) {
        LOG_ERR("nrfx_timer_init error: %08x", err);
        return;
    }

    uint32_t ticks = nrfx_timer_us_to_ticks(&timer_instance, SAADC_INTERVAL_US);
    nrfx_timer_extended_compare(&timer_instance, NRF_TIMER_CC_CHANNEL0, ticks, NRF_TIMER_SHORT_COMPARE0_CLEAR_MASK, false);
}



/* -----------------------------
 * SAADC event handler
 * ----------------------------- */
static void saadc_event_handler(nrfx_saadc_evt_t const * p_event)
{
    nrfx_err_t err;
    switch (p_event->type)
    {
        case NRFX_SAADC_EVT_READY:
        
           /* STEP 5.1 - Buffer is ready, timer (and sampling) can be started. */
            nrfx_timer_enable(&timer_instance);
            break;                        
            
        case NRFX_SAADC_EVT_BUF_REQ:
        
            /* STEP 5.2 - Set up the next available buffer. Alternate between buffer 0 and 1 */
            err = nrfx_saadc_buffer_set(saadc_sample_buffer[(saadc_current_buffer++)%2], SAADC_BUFFER_SIZE);
            //err = nrfx_saadc_buffer_set(saadc_sample_buffer[((saadc_current_buffer == 0 )? saadc_current_buffer++ : 0)], SAADC_BUFFER_SIZE);
            if (err != NRFX_SUCCESS) {
                LOG_ERR("nrfx_saadc_buffer_set error: %08x", err);
                return;
            }
            break;

        case NRFX_SAADC_EVT_DONE:

            /* NEW: pull out buffer pointer & size for later use */
            int16_t *buf = (int16_t *)p_event->data.done.p_buffer;
            size_t   n   = p_event->data.done.size;

            /* STEP 5.3 - Buffer has been filled. Do something with the data and proceed */
            int64_t average = 0;
            int16_t max = INT16_MIN;
            int16_t min = INT16_MAX;
            int16_t current_value; 
            for(int i=0; i < p_event->data.done.size; i++){
                current_value = ((int16_t *)(p_event->data.done.p_buffer))[i];
                average += current_value;
                if(current_value > max){
                    max = current_value;
                }
                if(current_value < min){
                    min = current_value;
                }
            }
            average = average/p_event->data.done.size;
            LOG_INF("SAADC buffer at 0x%x filled with %d samples", (uint32_t)p_event->data.done.p_buffer, p_event->data.done.size);
            LOG_INF("AVG=%d, MIN=%d, MAX=%d", (int16_t)average, min, max);

            SEGGER_RTT_Write(
                0,  // RTT buffer index
                (uint8_t *)p_event->data.done.p_buffer,
                p_event->data.done.size * sizeof(int16_t)
            );

                /* -------- NEW BLE-STAGING BLOCK (no changes above this line) -------- */

            if (n > SAADC_BUFFER_SIZE) {
                n = SAADC_BUFFER_SIZE;    // safety guard
            }

            memcpy(g_ble_buf, buf, n * sizeof(int16_t));
            g_ble_len = n;

            k_work_submit(&saadc_ble_work);
            
            break;
        default:
            LOG_INF("Unhandled SAADC evt %d", p_event->type);
            break;
    }
}




/* -----------------------------
 * Configure SAADC
 * ----------------------------- */
static void configure_saadc(void)
{
    nrfx_err_t err;

    /* STEP 4.4 - Connect ADC interrupt to nrfx interrupt handler */
    IRQ_CONNECT(DT_IRQN(DT_NODELABEL(adc)),
                DT_IRQ(DT_NODELABEL(adc), priority),
                nrfx_isr, nrfx_saadc_irq_handler, 0);


     /* STEP 4.5 - Initialize the nrfx_SAADC driver */
    err = nrfx_saadc_init(DT_IRQ(DT_NODELABEL(adc), priority));

    // sus
    irq_enable(DT_IRQN(DT_NODELABEL(adc)));   // <-- ADD THIS
    printk("[ADC] SAADC IRQ ENABLED\n");

    if (err != NRFX_SUCCESS) {
        LOG_ERR("nrfx_saadc_init error: %08x", err);
        return;
    }


    /* STEP 4.7 - Change gain config in default config and apply channel configuration */
    channel.channel_config.gain = NRF_SAADC_GAIN1_6;
    err = nrfx_saadc_channels_config(&channel, 1);
    if (err != NRFX_SUCCESS) {
        LOG_ERR("nrfx_saadc_channels_config error: %08x", err);
        return;
    }

    /* STEP 4.8 - Configure channel 0 in advanced mode with event handler (non-blocking mode) */
    nrfx_saadc_adv_config_t saadc_adv_config = NRFX_SAADC_DEFAULT_ADV_CONFIG;
    err = nrfx_saadc_advanced_mode_set(BIT(0),
                                        NRF_SAADC_RESOLUTION_12BIT,
                                        &saadc_adv_config,
                                        saadc_event_handler);
    if (err != NRFX_SUCCESS) {
        LOG_ERR("nrfx_saadc_advanced_mode_set error: %08x", err);
        return;
    }

    /* STEP 4.9 - Configure two buffers to make use of double-buffering feature of SAADC */
    err = nrfx_saadc_buffer_set(saadc_sample_buffer[0], SAADC_BUFFER_SIZE);
    if (err != NRFX_SUCCESS) {
        LOG_ERR("nrfx_saadc_buffer_set error: %08x", err);
        return;
    }
    err = nrfx_saadc_buffer_set(saadc_sample_buffer[1], SAADC_BUFFER_SIZE);
    if (err != NRFX_SUCCESS) {
        LOG_ERR("nrfx_saadc_buffer_set error: %08x", err);
        return;
    }


    /* STEP 4.10 - Trigger the SAADC. This will not start sampling, but will prepare buffer for sampling triggered through PPI */
    err = nrfx_saadc_mode_trigger();
    if (err != NRFX_SUCCESS) {
        LOG_ERR("nrfx_saadc_mode_trigger error: %08x", err);
        return;
    }

    // NEW
    k_work_init(&saadc_ble_work, saadc_ble_work_handler);

}


/* -----------------------------
 * Configure DPPI
 * ----------------------------- */
static void configure_ppi(void)
{
    nrfx_err_t err;
    /* STEP 6.1 - Declare variables used to hold the (D)PPI channel number */
    uint8_t m_saadc_sample_ppi_channel;
    uint8_t m_saadc_start_ppi_channel;

    /* STEP 6.2 - Trigger task sample from timer */
    err = nrfx_gppi_channel_alloc(&m_saadc_sample_ppi_channel);
    if (err != NRFX_SUCCESS) {
        LOG_ERR("nrfx_gppi_channel_alloc error: %08x", err);
        return;
    }

    err = nrfx_gppi_channel_alloc(&m_saadc_start_ppi_channel);
    if (err != NRFX_SUCCESS) {
        LOG_ERR("nrfx_gppi_channel_alloc error: %08x", err);
        return;
    }

    /* STEP 6.3 - Trigger task sample from timer */
    nrfx_gppi_channel_endpoints_setup(m_saadc_sample_ppi_channel, 
                                      nrfx_timer_compare_event_address_get(&timer_instance, NRF_TIMER_CC_CHANNEL0),
                                      nrf_saadc_task_address_get(NRF_SAADC, NRF_SAADC_TASK_SAMPLE));

    /* STEP 6.4 - Trigger task start from end event */
    nrfx_gppi_channel_endpoints_setup(m_saadc_start_ppi_channel, 
                                      nrf_saadc_event_address_get(NRF_SAADC, NRF_SAADC_EVENT_END),
                                      nrf_saadc_task_address_get(NRF_SAADC, NRF_SAADC_TASK_START));

    /* STEP 6.5 - Enable both (D)PPI channels */ 
    nrfx_gppi_channels_enable(BIT(m_saadc_sample_ppi_channel));
    nrfx_gppi_channels_enable(BIT(m_saadc_start_ppi_channel));
}


void saadc_start(void)
{
    configure_timer();
    configure_saadc();  
    configure_ppi();
}

/*
int main(void)
{
    configure_timer();
    configure_saadc();  
    configure_ppi();
    k_sleep(K_FOREVER);
}
*/

/* -----------------------------
 * PUBLIC API
 * ----------------------------- 
void adc_dma_init(void)
{
    printk("[ADC] adc_dma_init() called\n");
    printk("[ADC] Setting up SAADC, DMA, and TIMER\n");
    printk("[ADC] adc_dma_init() CALLED FROM %p\n", __builtin_return_address(0));
    printk("[ADC] timer already initialized? %d\n", nrfx_timer_is_enabled(&timer_instance));


    configure_saadc();    // init SAADC + buffer
    configure_timer();    // init timer
    configure_ppi();      // connect timer -> SAADC

    printk("[ADC] SAADC initialization complete (no calibration)\n");
}


void adc_dma_start(void)
{
    nrfx_timer_enable(&timer_instance);
}

void adc_dma_stop(void)
{
    nrfx_timer_disable(&timer_instance);
    nrfx_saadc_abort();
}
*/ 