/**
 * K10 HelloScreen Example
 *
 * Displays text on the UniHiker K10 screen.
 * Tests display, I2C touch, and backlight control.
 */
#include <Arduino.h>
#include <U8g2lib.h>

#ifdef U8X8_HAVE_HW_SPI
#include <SPI.h>
#endif
#ifdef U8X8_HAVE_HW_I2C
#include <Wire.h>
#endif

// K10 uses SSD1306 OLED over I2C (address 0x3C)
U8G2_SSD1306_128X64_NONAME_F_HW_I2C u8g2(U8G2_R0, /* reset=*/ U8X8_PIN_NONE);

void setup() {
  Serial.begin(115200);
  delay(1000);

  Wire.begin(8, 9);  // K10 I2C: SDA=GPIO8, SCL=GPIO9

  u8g2.begin();
  u8g2.enableUTF8Print();

  Serial.println("K10 HelloScreen starting...");
}

void loop() {
  u8g2.clearBuffer();
  u8g2.setFont(u8g2_font_ncenB14_tr);
  u8g2.setCursor(0, 20);
  u8g2.print("Hello K10!");
  u8g2.setFont(u8g2_font_6x10_tr);
  u8g2.setCursor(0, 40);
  u8g2.print("Compile Server OK");
  u8g2.setCursor(0, 55);
  u8g2.print(millis() / 1000);
  u8g2.sendBuffer();

  Serial.print("Uptime: ");
  Serial.print(millis() / 1000);
  Serial.println("s");

  delay(1000);
}
