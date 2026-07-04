/**
 * K10 Blink Example
 * 
 * Flashes the built-in LED on the UniHiker K10.
 * Use this as a minimal test after setting up the compile server.
 */
#include <Arduino.h>

#define LED_BUILTIN 14

void setup() {
  pinMode(LED_BUILTIN, OUTPUT);
  Serial.begin(115200);
  delay(1000);
  Serial.println("K10 Blink starting...");
}

void loop() {
  digitalWrite(LED_BUILTIN, HIGH);
  Serial.println("ON");
  delay(500);
  digitalWrite(LED_BUILTIN, LOW);
  Serial.println("OFF");
  delay(500);
}
