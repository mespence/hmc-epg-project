/*
 * NAME: Julia Hansen
 * EMAIL: juhansen@g.hmc.edu
 * DATE: 03/04/25
 * PURPOSE: This file allows Bluetooth to be run on the nrf5340 dk
*/

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/uuid.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/kernel.h>
#include <zephyr/drivers/adc.h>
#include <zephyr/logging/log.h>
#include <string.h>
#include <stdlib.h> 
#include <math.h>

#include "../include/bluetooth.h"
#include "../include/dds.h"
#include "../include/gpio.h"

#define RECEIVE_BUFF_SIZE 20
#define BT_LE_ADV_CONN_CUSTOM BT_LE_ADV_PARAM(BT_LE_ADV_OPT_CONNECTABLE, 0x0020, 0x0020, NULL )

LOG_MODULE_REGISTER(ble_test); 

volatile bool ble_ready =false; // Flag indicating status of BLE
static char msg[32];

// Define the receive buffer
static uint8_t write_buffer[RECEIVE_BUFF_SIZE] = {0};

// Workqueue Things
static int digipot_channel = 0;
static int digipot_setting = 0;

static int pga_number = 0;
static int pga_setting = 0;

static volatile bool start_received = false;
static volatile bool on_received = false;

/*
 It is possible to adjust the connection settings on the BLE by editing 
 the BT_GAP_INIT_CONN_INT_MIN and BT_GAP_INIT_CONN_INT_MAX params in the
 gap.h file in the conn.h file. Note that the units are in 1.25ms so the
 actual value will be N*1.25ms.  
 You can also adjust the data length which is the number of bytes sent in 
 one packet by adjusting BT_GAP_ADV_MAX_ADV_DATA_LEN in gap.h. 
*/

// Advertisement data 
static const struct bt_data ad[] = {
	BT_DATA_BYTES(BT_DATA_FLAGS, (BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR)), // Using BLE
	BT_DATA_BYTES(BT_DATA_UUID128_ALL, BT_UUID_MY_CUSTOM_SERV_VAL), //Custom service 
};


// Write callback function
// Return value in buffer and decode it 
ssize_t write_custom_value(struct bt_conn *conn, 
				const struct bt_gatt_attr *attr, const void *buf, 
				uint16_t len, uint16_t offset, uint8_t flags) { 
					printk("BLE WRITE CALLBACK FIRED. len=%d\n", len);
					memcpy(write_buffer, buf, len); 
					printk("Received data from Python: %s\n", write_buffer); 
					write_buffer[len] = '\0';  
					// FREQUENCY SETTING
					if (write_buffer[0] == 'S' && write_buffer[1] == 'D' && write_buffer[2] == 'D' && write_buffer[3] == 'S' && write_buffer[4] == ':') {
						uint16_t val = atoi(&write_buffer[5]);  
						//printk("Received value: %f\n", val);  
						changeDDSVal(val);
						return len;
					// DDS GAIN SETTING
					} else if (write_buffer[0] == 'D' && write_buffer[1] == 'D' && write_buffer[2] == 'S' && write_buffer[3] == 'A' && write_buffer[4] == ':') {
						double amplification = atof(&write_buffer[5]);  // Convert string to double
						printk("amp: %.6f\n", amplification);
						if (amplification >= MIN_DDS_AMPLIFICATION && amplification <= MAX_DDS_AMPLIFICATION) {
							printk("Setting DDS amplification to %.2fx\n", amplification);
							set_dds_amplification(amplification);
						} else {
							printk("Invalid DDS amplification command!\n");
						}
						return len;
					// DDS OFFSET SETTING
					} else if (write_buffer[0] == 'D' && write_buffer[1] == 'D' && write_buffer[2] == 'S' && write_buffer[3] == 'O' && write_buffer[4] == ':'){
						double offset = atof(&write_buffer[5]);
						printk("offset: %.6f\n", offset);

						if (offset >= -3.3 && offset <= 3.3) {
							printk("Setting DDS offset to %.2fV\n", offset);
							set_dds_offset(offset);
						} else {
							printk("Invalid DDS offset command!\n");
						}
						return len;
					// PGA SETTING
					} else if (write_buffer[0] == 'P' && write_buffer[2] == ':') {  
						uint16_t number = write_buffer[1] - '0'; // Convert char to int
						uint16_t setting = atoi(&write_buffer[3]);  // Convert string to int

						if (number >= 1 && number <= 2 && setting >= 0 && setting <= 7) {
							pga_number = number;
							pga_setting = setting;

							printk("Setting PGA %d to value %d\n", pga_number, pga_setting);
							configure_pga(pga_number, pga_setting);
						} else {
							printk("Invalid pga command!\n");
						}
						return len;
					// DIGIPOT SETTING
					} else if(write_buffer[0] == 'D' && write_buffer[2] == ':') {
						int channel = write_buffer[1] - '0'; // Convert char to int
						int value = atoi(&write_buffer[3]);  // Convert string to int
				
						if (channel >= 0 && channel <= 3 && value >= 0 && value <= 255) {
							digipot_channel = channel;
							digipot_setting = value;
				
							printk("Setting Digipot %d to value %d\n", digipot_channel, digipot_setting);
							digipot_wiper_set(digipot_channel, digipot_setting);
						} else {
							printk("Invalid digipot command!\n");
						}
						return len;
					// MUX Setting
					} else if (write_buffer[0] == 'M' && write_buffer[1] == ':' && write_buffer[2] >= '0' && write_buffer[2] <= '7') {
						int setting = write_buffer[2] - '0'; // Convert char to int
				
						if (setting >= 0 && setting <= 7) {
							printk("Setting Mux to setting %d\n", setting);
							set_mux(setting);
						} else {
							printk("Invalid mux command!\n");
						}
						return len;
					// SIGNAL CHAIN OFFSET Setting
					} else if (write_buffer[0] == 'S' && write_buffer[1] == 'C' && write_buffer[2] == 'O' && write_buffer[3] == ':') {
						double offset = atof(&write_buffer[4]);  // Convert string to double
				
						if (offset >= -3.3 && offset <= 3.3) {
							printk("Setting signal chain offset to %.2fV\n", offset);
							set_signal_chain_offset(offset);
						} else {
							printk("Invalid signal chain offset command!\n");
						}
						return len;
					// SIGNAL CHAIN AMPLIFICATION Setting
					} else if (write_buffer[0] == 'S' && write_buffer[1] == 'C' && write_buffer[2] == 'A' && write_buffer[3] == ':') {
						double gain = atof(&write_buffer[4]);  // Convert string to double
				
						if (gain >= 1.0 && gain <= 7000.0) {
							printk("Setting signal chain amplification to %.2fx\n", gain);
							set_signal_chain_amplification(gain);
						} else {
							printk("Invalid signal chain amplification command!\n");
						}
						return len;
					// IDDS Setting
					} else if (write_buffer[0] == 'I' && write_buffer[1] == 'D' && write_buffer[2] == 'D' && write_buffer[3] == 'S') {
						printk("Starting DDS output\n");
						start_dds(1000);  // Start DDS with 1kHz sine wave
						return len;
					// DDSOFF Setting
					} else if (write_buffer[0] == 'D' && write_buffer[1] == 'D' && write_buffer[2] == 'S' && write_buffer[3] == 'O' && write_buffer[4] == 'F' && write_buffer[5] == 'F') {
						LOG_INF("Stopping DDS output\n");
						dds_sleep();  // Stop DDS output
						return len;
					// ON Setting
					} else if (write_buffer[0] == 'O' && write_buffer[1] == 'N') {
						on_received = true;
						LOG_INF("Powering up the system...");
						power_up();
						LOG_INF("Power up complete!");
						return len;
					// START Setting
					} else if (write_buffer[0] == 'S' && write_buffer[1] == 'T' && write_buffer[2] == 'A' && write_buffer[3] == 'R' && write_buffer[4] == 'T'){
						start_received = true;
						//printk("start:", start_received);
						return len;
					// OFF Setting
					} else if (write_buffer[0] == 'O' && write_buffer[1] == 'F' && write_buffer[2] == 'F') {
						LOG_INF("Powering down the system...\n");
						power_down();
						return len;
					}
				}

// Define Custom Service
BT_GATT_SERVICE_DEFINE(custom_srv,
		BT_GATT_PRIMARY_SERVICE(BT_UUID_MY_CUSTOM_SERVICE),
		// Notify Python whenever new adc data is available to send with notification param
		BT_GATT_CHARACTERISTIC(BT_UUID_MY_ADC_CHRC, BT_GATT_CHRC_NOTIFY, BT_GATT_PERM_NONE, NULL , NULL, NULL),
		BT_GATT_CCC(adc_ccc_cfg_changed, BT_GATT_PERM_READ | BT_GATT_PERM_WRITE), // responsible for sending notifications, contains the value
		// Setting write characteristic to write data from client to the characteristic
		BT_GATT_CHARACTERISTIC(BT_UUID_PY_READ_CHRC, BT_GATT_CHRC_WRITE, BT_GATT_PERM_WRITE, NULL, write_custom_value, write_buffer),
);

static bool notify_enabled = false;

// Debug function to ensure we can notify Python
static void adc_ccc_cfg_changed(const struct bt_gatt_attr *attr, uint16_t value)
{
    notify_enabled = (value == BT_GATT_CCC_NOTIFY);
    printk("Notify enabled: %s\n", notify_enabled ? "true" : "false");
}

// Callback function for when BT is ready
void bt_ready(int err){
	if (err) {
		LOG_ERR("bt enable return %d", err);
	}
	LOG_INF("bt_ready!");
	ble_ready=true;
}

// Initializing BT and calling bt_ready when BT is 
// initialized and ready for connection
int init_ble(void) {
	LOG_INF("Init BLE");
	int err;
	err = bt_enable(bt_ready);
	if (err) {
		LOG_ERR("bt_enable failed (err %d)", err);
		return err; 
	}

	return 0;
}


bool ble_connected = false;

static void connected(struct bt_conn *conn, uint8_t err)
{
    if (!err) {
        ble_connected = true;
        LOG_INF("BLE connected");
    }
}

static void disconnected(struct bt_conn *conn, uint8_t reason)
{
    ble_connected = false;
    notify_enabled = false;  // very important
    LOG_INF("BLE disconnected (reason %u)", reason);
}


static ssize_t cccd_cfg_changed(const struct bt_gatt_attr *attr,
                                uint16_t value)
{
    notify_enabled = (value == BT_GATT_CCC_NOTIFY);
    LOG_INF("Notify enabled: %d", notify_enabled);
    return 0;
}


void ble_send_adc_chunk(const uint8_t *data, size_t len)
{
	 LOG_INF("[BLE] ble_send_adc_chunk called, notify_enabled=%d len=%d",
        notify_enabled, (int)len);
		
    if (!ble_connected || !notify_enabled) {
        return;
    }

    int ret = bt_gatt_notify(NULL, &custom_srv.attrs[1], data, len);
    if (ret < 0) {
        LOG_WRN("[BLE] bt_gatt_notify (ADC chunk) failed, len=%d err=%d", (int)len, ret);
    }
}






// Keep track of start variable
bool ble_start_received(void){
	return start_received;
}

// Keep track of on variable
bool ble_on_received(void) {
	return on_received;
}

// Start up BLE
int start_ble(void) {
	init_ble();
	while(!ble_ready){
		LOG_INF("BLE stack not ready yet");
		k_msleep(100);
	}
	LOG_INF("BLE stack ready!");

	int err;
	// Start advertising 
	err = bt_le_adv_start(BT_LE_ADV_CONN_CUSTOM, ad, ARRAY_SIZE(ad), NULL, 0); // Only need one advertisement package
	if (err) {
		printk("Advertising failed to start (err %d)\n", err);
		return 0;
	}

	return 0;

}
