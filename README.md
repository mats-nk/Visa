# "Visa" Introduction

Visa is old norse for "Show" and thats what this project is about.

The core is to show time/date and weather in a local language on a MAX7219 display (others could be added in the future).

Other data can be added to "Visa" from other sources that has HTTP API and preferable Json.

As display is MAX7219 modules (4-16) is choosen, but other displays like MDM (P2-P10) could be added in the future.

**Others that would like to contribute is welcome**

## Thanks to

**Marco Colli**, MajicDesigns. Marcos libraries and his helpfull attitude have inspired me a lot.

**Bodmer** for his http://www.openweathermap.org library for Arduino.

**OpenWeatherMap** http://openweathermap.org for having a free weather API (API Key registration is needed).

**Brian Lough** Excellent YouTube videos about Json and the helpfull discord server he has. https://www.youtube.com/@BrianLough


## Features

- Web interface to configure the display (MAX7219)
- Configurable MAX7219 Hardware type (FC16, Generic, Parola or ICstation)) and no of MAX7219 modules (4-16) via Web interface
- Fetch weather data - OpenWeatherMap
- Get correct time and date - NTP
- Movment (PIR) sensor for trigger "show clock" when clock is in sleep mode and trigger "show weather" when clock is NOT sleeping
- Button for multiple purposes (trigger reset to factory settings ++)
- LED to show activity
- LDR (Light Depending Resistor) to automatically adjust the display intensity

The languages that the project will support initialy is
- English
- Swedish, Norvegian, dannish
- German
- Other languages is to be community driven, with the help of translation templates...

## Later

- Temp sensor (DS18B20 or EQ)
- Moisture sensor (DHT21, BME280 or EQ)
- LuftDaten (Air quality)
- News
- Electricy/energy price
- Other ...
