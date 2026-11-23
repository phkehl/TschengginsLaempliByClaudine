// RTTTL melodies: built-in list and parser
//
// Ported from the old firmware (https://github.com/phkehl/TschengginsLaempli, src/rtttl.c). That code was based on:
// - https://github.com/esp8266/Arduino/blob/master/cores/esp8266/Tone.cpp
// - hw_timer.c from the SDK
// - Arduino ESP8266 code
// - http://domoticx.com/arduino-melodie-afspelen-rtttl/ (melodies, parser: Copyright 2017 DomoticX)
// The parser here is new (it reads one note at a time from flash, checks the input). The melody sources are below.
//
// Note frequencies: equal temperament, A4 = 440 Hz, as the Arduino "toneMelody" example (pitches.h) that the old
// firmware used.
//
// ---------------------------------------------------------------------------------------------------------------------
//
// http://merwin.bespin.org/t4a/specifications.html / http://merwin.bespin.org/t4a/specs/nokia_rtttl.txt:
// RTTTL Format Specifications
//
// RTTTL (RingTone Text Transfer Language) is the primary format used to distribute
// ringtones for Nokia phones. An RTTTL file is a text file, containing the
// ringtone name, a control section and a section containing a comma separated
// sequence of ring tone commands. White space must be ignored by any reader
// application.
//
// Example:
// Simpsons:d=4,o=5,b=160:32p,c.6,e6,f#6,8a6,g.6,e6,c6,8a,8f#,8f#,8f#,2g
//
// This file describes a ringtone whose name is 'Simpsons'. The control section
// sets the beats per minute at 160, the default note length as 4, and the default
// scale as Octave 5.
// <RTX file> := <name> ":" [<control section>] ":" <tone-commands>
//
//     <name> := <char> ; maximum name length 10 characters
//
//     <control-section> := <control-pair> ["," <control-section>]
//
//         <control-pair> := <control-name> ["="] <control-value>
//
//         <control-name> := "o" | "d" | "b"
//         ; Valid in control section: o=default scale, d=default duration, b=default beats per minute.
//         ; if not specified, defaults are 4=duration, 6=scale, 63=beats-per-minute
//         ; any unknown control-names must be ignored
//
//         <tone-commands> := <tone-command> ["," <tone-commands>]
//
//         <tone-command> :=<note> | <control-pair>
//
//         <note> := [<duration>] <note> [<scale>] [<special-duration>] <delimiter>
//
//             <duration> := "1" | "2" | "4" | "8" | "16" | "32"
//             ; duration is divider of full note duration, eg. 4 represents a quarter note
//
//             <note> := "P" | "C" | "C#" | "D" | "D#" | "E" | "F" | "F#" | "G" | "G#" | "A" | "A#" | "B"
//
//             <scale> :="4" | "5" | "6" | "7"
//             ; Note that octave 4: A=440Hz, 5: A=880Hz, 6: A=1.76 kHz, 7: A=3.52 kHz
//             ; The lowest note on the Nokia 61xx is A4, the highest is B7
//
//             <special-duration> := "." ; Dotted note
//
// ; End of specification
//
// ---------------------------------------------------------------------------------------------------------------------
//
// More than the specification (the melodies use it): the dot also before the scale ("c.6"), "e#" (= f), octaves
// 1-8. Unknown characters in a note: the note is skipped.

#include <Arduino.h>

#include <math.h>

#include "rtttl.hpp"

// Melodies, in flash
// from http://domoticx.com/arduino-melodieafspelen-rtttl
static const char MELODY_01[] PROGMEM =
    "TheSimpsons:d=4,o=5,b=160:c.6,e6,f#6,8a6,g.6,e6,c6,8a,8f#,8f#,8f#,2g,8p,8p,8f#,8f#,8f#,8g,a#.,8c6,8c6,8c6,c6";
static const char MELODY_02[] PROGMEM =
    "Indiana:d=4,o=5,b=250:e,8p,8f,8g,8p,1c6,8p.,d,8p,8e,1f,p.,g,8p,8a,8b,8p,1f6,p,a,8p,8b,2c6,2d6,2e6,e,8p,8f,8g,"
    "8p,1c6,p,d6,8p,8e6,1f.6,g,8p,8g,e.6,8p,d6,8p,8g,e.6,8p,d6,8p,8g,f.6,8p,e6,8p,8d6,2c6";
static const char MELODY_03[] PROGMEM =
    "IndianaShort:d=4,o=5,b=250:e,8p,8f,8g,8p,1c6,8p.,d,8p,8e,1f,p.";
static const char MELODY_04[] PROGMEM =
    "TakeOnMe:d=4,o=4,b=160:8f#5,8f#5,8f#5,8d5,8p,8b,8p,8e5,8p,8e5,8p,8e5,8g#5,8g#5,8a5,8b5,8a5,8a5,8a5,8e5,8p,8d5,"
    "8p,8f#5,8p,8f#5,8p,8f#5,8e5,8e5,8f#5,8e5,8f#5,8f#5,8f#5,8d5,8p,8b,8p,8e5,8p,8e5,8p,8e5,8g#5,8g#5,8a5,8b5,8a5,"
    "8a5,8a5,8e5,8p,8d5,8p,8f#5,8p,8f#5,8p,8f#5,8e5,8e5";
static const char MELODY_05[] PROGMEM =
    "Entertainer:d=4,o=5,b=140:8d,8d#,8e,c6,8e,c6,8e,2c.6,8c6,8d6,8d#6,8e6,8c6,8d6,e6,8b,d6,2c6,p,8d,8d#,8e,c6,8e,"
    "c6,8e,2c.6,8p,8a,8g,8f#,8a,8c6,e6,8d6,8c6,8a,2d6";
static const char MELODY_06[] PROGMEM =
    "Muppets:d=4,o=5,b=250:c6,c6,a,b,8a,b,g,p,c6,c6,a,8b,8a,8p,g.,p,e,e,g,f,8e,f,8c6,8c,8d,e,8e,8e,8p,8e,g,2p,c6,"
    "c6,a,b,8a,b,g,p,c6,c6,a,8b,a,g.,p,e,e,g,f,8e,f,8c6,8c,8d,e,8e,d,8d,c";
static const char MELODY_07[] PROGMEM =
    "Xfiles:d=4,o=5,b=125:e,b,a,b,d6,2b.,1p,e,b,a,b,e6,2b.,1p,g6,f#6,e6,d6,e6,2b.,1p,g6,f#6,e6,d6,f#6,2b.,1p,e,b,a,"
    "b,d6,2b.,1p,e,b,a,b,e6,2b.,1p,e6,2b.";
static const char MELODY_08[] PROGMEM =
    "Looney:d=4,o=5,b=140:32p,c6,8f6,8e6,8d6,8c6,a.,8c6,8f6,8e6,8d6,8d#6,e.6,8e6,8e6,8c6,8d6,8c6,8e6,8c6,8d6,8a,"
    "8c6,8g,8a#,8a,8f";
static const char MELODY_09[] PROGMEM =
    "20thCenFox:d=16,o=5,b=140:b,8p,b,b,2b,p,c6,32p,b,32p,c6,32p,b,32p,c6,32p,b,8p,b,b,b,32p,b,32p,b,32p,b,32p,b,"
    "32p,b,32p,b,32p,g#,32p,a,32p,b,8p,b,b,2b,4p,8e,8g#,8b,1c#6,8f#,8a,8c#6,1e6,8a,8c#6,8e6,1e6,8b,8g#,8a,2b";
static const char MELODY_10[] PROGMEM =
    "Bond:d=4,o=5,b=80:32p,16c#6,32d#6,32d#6,16d#6,8d#6,16c#6,16c#6,16c#6,16c#6,32e6,32e6,16e6,8e6,16d#6,16d#6,"
    "16d#6,16c#6,32d#6,32d#6,16d#6,8d#6,16c#6,16c#6,16c#6,16c#6,32e6,32e6,16e6,8e6,16d#6,16d6,16c#6,16c#7,c.7,"
    "16g#6,16f#6,g#.6";
static const char MELODY_11[] PROGMEM =
    "MASH:d=8,o=5,b=140:4a,4g,f#,g,p,f#,p,g,p,f#,p,2e.,p,f#,e,4f#,e,f#,p,e,p,4d.,p,f#,4e,d,e,p,d,p,e,p,d,p,2c#.,p,"
    "d,c#,4d,c#,d,p,e,p,4f#,p,a,p,4b,a,b,p,a,p,b,p,2a.,4p,a,b,a,4b,a,b,p,2a.,a,4f#,a,b,p,d6,p,4e.6,d6,b,p,a,p,2b";
static const char MELODY_12[] PROGMEM =
    "StarWars:d=4,o=5,b=45:32p,32f#,32f#,32f#,8b.,8f#.6,32e6,32d#6,32c#6,8b.6,16f#.6,32e6,32d#6,32c#6,8b.6,16f#.6,"
    "32e6,32d#6,32e6,8c#.6,32f#,32f#,32f#,8b.,8f#.6,32e6,32d#6,32c#6,8b.6,16f#.6,32e6,32d#6,32c#6,8b.6,16f#.6,32e6,"
    "32d#6,32e6,8c#6";
static const char MELODY_13[] PROGMEM =
    "GoodBad:d=4,o=5,b=56:32p,32a#,32d#6,32a#,32d#6,8a#.,16f#.,16g#.,d#,32a#,32d#6,32a#,32d#6,8a#.,16f#.,16g#.,c#6,"
    "32a#,32d#6,32a#,32d#6,8a#.,16f#.,32f.,32d#.,c#,32a#,32d#6,32a#,32d#6,8a#.,16g#.,d#";
static const char MELODY_14[] PROGMEM =
    "TopGun:d=4,o=4,b=31:32p,16c#,16g#,16g#,32f#,32f,32f#,32f,16d#,16d#,32c#,32d#,16f,32d#,32f,16f#,32f,32c#,16f,"
    "d#,16c#,16g#,16g#,32f#,32f,32f#,32f,16d#,16d#,32c#,32d#,16f,32d#,32f,16f#,32f,32c#,g#";
static const char MELODY_15[] PROGMEM =
    "A-Team:d=8,o=5,b=125:4d#6,a#,2d#6,16p,g#,4a#,4d#.,p,16g,16a#,d#6,a#,f6,2d#6,16p,c#.6,16c6,16a#,g#.,2a#";
static const char MELODY_16[] PROGMEM =
    "Flinstones:d=4,o=5,b=40:32p,16f6,16a#,16a#6,32g6,16f6,16a#.,16f6,32d#6,32d6,32d6,32d#6,32f6,16a#,16c6,d6,16f6,"
    "16a#.,16a#6,32g6,16f6,16a#.,32f6,32f6,32d#6,32d6,32d6,32d#6,32f6,16a#,16c6,a#,16a6,16d.6,16a#6,32a6,32a6,32g6,"
    "32f#6,32a6,8g6,16g6,16c.6,32a6,32a6,32g6,32g6,32f6,32e6,32g6,8f6,16f6,16a#.,16a#6,32g6,16f6,16a#.,16f6,32d#6,"
    "32d6,32d6,32d#6,32f6,16a#,16c.6,32d6,32d#6,32f6,16a#,16c.6,32d6,32d#6,32f6,16a#6,16c7,8a#.6";
static const char MELODY_17[] PROGMEM =
    "Jeopardy:d=4,o=6,b=125:c,f,c,f5,c,f,2c,c,f,c,f,a.,8g,8f,8e,8d,8c#,c,f,c,f5,c,f,2c,f.,8d,c,a#5,a5,g5,f5,p,d#,"
    "g#,d#,g#5,d#,g#,2d#,d#,g#,d#,g#,c.7,8a#,8g#,8g,8f,8e,d#,g#,d#,g#5,d#,g#,2d#,g#.,8f,d#,c#,c,p,a#5,p,g#.5,d#,g#";
static const char MELODY_18[] PROGMEM =
    "Gadget:d=16,o=5,b=50:32d#,32f,32f#,32g#,a#,f#,a,f,g#,f#,32d#,32f,32f#,32g#,a#,d#6,4d6,32d#,32f,32f#,32g#,a#,"
    "f#,a,f,g#,f#,8d#";
static const char MELODY_19[] PROGMEM =
    "Smurfs:d=32,o=5,b=200:4c#6,16p,4f#6,p,16c#6,p,8d#6,p,8b,p,4g#,16p,4c#6,p,16a#,p,8f#,p,8a#,p,4g#,4p,g#,p,a#,p,"
    "b,p,c6,p,4c#6,16p,4f#6,p,16c#6,p,8d#6,p,8b,p,4g#,16p,4c#6,p,16a#,p,8b,p,8f,p,4f#";
static const char MELODY_20[] PROGMEM =
    "MahnaMahna:d=16,o=6,b=125:c#,c.,b5,8a#.5,8f.,4g#,a#,g.,4d#,8p,c#,c.,b5,8a#.5,8f.,g#.,8a#.,4g,8p,c#,c.,b5,"
    "8a#.5,8f.,4g#,f,g.,8d#.,f,g.,8d#.,f,8g,8d#.,f,8g,d#,8c,a#5,8d#.,8d#.,4d#,8d#.";
static const char MELODY_21[] PROGMEM =
    "LeisureSuit:d=16,o=6,b=56:f.5,f#.5,g.5,g#5,32a#5,f5,g#.5,a#.5,32f5,g#5,32a#5,g#5,8c#.,a#5,32c#,a5,a#.5,c#.,"
    "32a5,a#5,32c#,d#,8e,c#.,f.,f.,f.,f.,f,32e,d#,8d,a#.5,e,32f,e,32f,c#,d#.,c#";
static const char MELODY_22[] PROGMEM =
    "MissionImp:d=16,o=6,b=95:32d,32d#,32d,32d#,32d,32d#,32d,32d#,32d,32d,32d#,32e,32f,32f#,32g,g,8p,g,8p,a#,p,c7,"
    "p,g,8p,g,8p,f,p,f#,p,g,8p,g,8p,a#,p,c7,p,g,8p,g,8p,f,p,f#,p,a#,g,2d,32p,a#,g,2c#,32p,a#,g,2c,a#5,8c,2p,32p,"
    "a#5,g5,2f#,32p,a#5,g5,2f,32p,a#5,g5,2e,d#,8d";
// from http://www.lucodes.ca/ringtones.html
static const char MELODY_23[] PROGMEM =
    "PacMan:d=16,o=6,b=140:b5,b,f#,d#,8b,8d#,c,c7,g,f,8c7,8e,b5,b,f#,d#,8b,8d#,32d#,32e,f,32f,32f#,g,32g,32g#,a,8b";
static const char MELODY_24[] PROGMEM =
    "HappyBday:d=4,o=5,b=125:8d.,16d,e,d,g,2f#,8d.,16d,e,d,a,2g,8d.,16d,d6,b,g,f#,2e,8c.6,16c6,b,g,a,2g";
static const char MELODY_25[] PROGMEM =
    "Titanic:d=8,o=6,b=125:e,f#,2g#,16f#,16g#,16f#,e,f#,2b,32c#7,32b,a,g#,4e,2c#,2b5,d#,f#,f#,2g#,16a,16g#,16f#,"
    "16e,4f#,2b,g#,b,2c#7,2b,2f#";
static const char MELODY_26[] PROGMEM =
    "Urgent:d=8,o=6,b=500:c,e,d7,c,e,a#,c,e,a,c,e,g,c,e,a,c,e,a#,c,e,d7";
// from http://arcadetones.emuunlim.com/
static const char MELODY_27[] PROGMEM =
    "SuperMario1:d=4,o=5,b=100:16e6,16e6,32p,8e6,16c6,8e6,8g6,8p,8g,8p,8c6,16p,8g,16p,8e,16p,8a,8b,16a#,8a,16g.,"
    "16e6,16g6,8a6,16f6,8g6,8e6,16c6,16d6,8b,16p,8c6,16p,8g,16p,8e,16p,8a,8b,16a#,8a,16g.,16e6,16g6,8a6,16f6,8g6,"
    "8e6,16c6,16d6,8b,8p,16g6,16f#6,16f6,16d#6,16p,16e6,16p,16g#,16a,16c6,16p,16a,16c6,16d6,8p,16g6,16f#6,16f6,"
    "16d#6,16p,16e6,16p,16c7,16p,16c7,16c7,p,16g6,16f#6,16f6,16d#6,16p,16e6,16p,16g#,16a,16c6,16p,16a,16c6,16d6,8p,"
    "16d#6,8p,16d6,8p,16c6";
static const char MELODY_28[] PROGMEM =
    "SuperMario2:d=4,o=6,b=100:32c,32p,32c7,32p,32a5,32p,32a,32p,32a#5,32p,32a#,2p,32c,32p,32c7,32p,32a5,32p,32a,"
    "32p,32a#5,32p,32a#,2p,32f5,32p,32f,32p,32d5,32p,32d,32p,32d#5,32p,32d#,2p,32f5,32p,32f,32p,32d5,32p,32d,32p,"
    "32d#5,32p,32d#";
static const char MELODY_29[] PROGMEM =
    "SuperMario3:d=4,o=5,b=90:32c6,32c6,32c6,8p,16b,16f6,16p,16f6,16f.6,16e.6,16d6,16c6,16p,16e,16p,16c";
static const char MELODY_30[] PROGMEM =
    "Tetris1:d=4,o=5,b=160:e6,8b,8c6,8d6,16e6,16d6,8c6,8b,a,8a,8c6,e6,8d6,8c6,b,8b,8c6,d6,e6,c6,a,2a,8p,d6,8f6,a6,"
    "8g6,8f6,e6,8e6,8c6,e6,8d6,8c6,b,8b,8c6,d6,e6,c6,a,a";
static const char MELODY_31[] PROGMEM =
    "Tetris2:d=4,o=5,b=160:d6,32p,c.6,32p,8a,8c6,8a#,16a,16g,f,c,8a,8c6,8g,8a,f,c,d,8d,8e,8g,8f,8e,8d,c,c,c";
static const char MELODY_32[] PROGMEM =
    "Tetris3:d=4,o=5,b=63:d#6,b,c#6,a#,16b,16g#,16a#,16b,16b,16g#,16a#,16b,c#6,g,d#6,16p,16g#,16a#,16b,c#6,16p,16b,"
    "16a#,g#,g,g#,16f,16g,16g#,16a#,8d#.6,32d#6,32p,32d#6,32p,32d#6,32p,16d6,16d#6,8f.6,16d6,8a#,8p,8f#6,8d#6,8f#,"
    "8g#,a#.,16p,16a#,8d#.6,16f6,16f#6,16f6,16d#6,16a#,8g#.,16b,8d#6,16f6,16d#6,8a#.,16b,16a#,16g#,16f,16f#,d#";
static const char MELODY_33[] PROGMEM =
    "BubbleBobble:d=4,o=5,b=125:8a#6,8a6,8g.6,16f6,8a6,8g6,8f6,8d#6,8g6,8f6,16d#6,8d.6,f.6,16d6,16c6,8a#,8c6,8d6,"
    "8d#6,8c6,16d6,8d#.6,8f6,8f6,8g6,16a6,8g.6,8f6,8f6,8g6,8a6,8a#6,8a6,8g.6,16f6,8a6,8g6,8f6,8d#6,8g6,8f6,16d#6,"
    "8d.6,f.6,16d6,16c6,8a#,8c6,8d6,8d#6,8c6,16d6,8d#.6,8f6,8f6,8g6,16a6,8f.6,8a#.6";
static const char MELODY_34[] PROGMEM =
    "Duke3d:d=4,o=5,b=90:16f#4,16a4,16p,16b4,8p,16f#4,16b4,16p,16c#,8p,16f#4,16c#,16p,16d,16p,16f#4,16p,8c#,16b4,"
    "16a4,16f#4,16e4,16f4,16f#4,16g4,16b4,16a4,16g4,16f#4,16a4,16p,16f#4,16a4,16p,16b4,8p,16f#4,16b4,16p,16c#,8p,"
    "16f#4,16c#,16p,16d,16p,16f#4,16p,8c#,16b4,16a4,16f#4,16e4,16f4,16f#4,16g4,16b4,16a4,16g4,16f#4,16a4";
static const char MELODY_35[] PROGMEM =
    "ManiacMansion:d=4,o=5,b=112:8f#4,16f#4,16f#4,8f#,16f#4,8f#4,16f#,16f#4,16f#4,8f#,8f#4,8g#4,16g#4,16g#4,8g#,"
    "16g#4,8g#4,16g#,16g#4,16g#4,8g#,8g#4,8a4,16a4,16a4,8a,16a4,8a4,16a,16a4,16a4,8a,8a4,8b4,16b4,16b4,8b,16b4,8b4,"
    "16b,8b4,16f#,16b4,8a4,8f#4,16f#4,16f#4,8c#,16f#4,8f#4,16c#,16d#4,16d#4,8e4,8f4,8f#4,16f#4,16f#4,8c#,16f#4,"
    "8f#4,16c#,16d#4,16d#4,8e4,8f4,8f#4,16f#4,16f#4,8c#,16f#4,8f#4,16c#,16d#4,16d#4,8e4,8f4,8f#4";
static const char MELODY_36[] PROGMEM =
    "Dott:d=4,o=5,b=125:8d#6,16e6,16p,16c#6,16p,16a,16p,16e,16p,d#.,16p,8d#,16e,16p,16a,16p,16c#6,16p,16e6,16p,d#6,"
    "f6,8e#.6";
static const char MELODY_37[] PROGMEM =
    "ImperialMarch:d=4,o=5,b=80:8d.,8d.,8d.,8a#4,16f,8d.,8a#4,16f,d.,32p,8a.,8a.,8a.,8a#,16f,8c#.,8a#4,16f,d.,32p,"
    "8d.6,8d,16d,8d6,32p,8c#6,16c6,16b,16a#,8b,32p,16d#,8g#,32p,8g,16f#,16f,16e,8f,32p,16a#4,8c#,32p,8a#4,16c#,8f.,"
    "8d,16f,a.,32p,8d.6,8d,16d,8d6,32p,8c#6,16c6,16b,16a#,8b,32p,16d#,8g#,32p,8g,16f#,16f,16e,8f,32p,16a#4,8c#,32p,"
    "8a#4,16f,8d.,8a#4,16f,d.";
static const char MELODY_38[] PROGMEM =
    "ImperialShort:d=4,o=5,b=80:8d.,8d.,8d.,8a#4,16f,8d.,8a#4,16f,d.";
static const char MELODY_39[] PROGMEM =
    "Beethoven5th:d=16,o=5,b=100:g,g,g,4d#,4p,f,f,f,4d,4p,g,g,g,d#,g#,g#,g#,g,d#6,d#6,d#6,4c6,8p,g,g,g,d,g#,g#,g#,"
    "g,f6,f6,f6,4d6,8p,g6,g6,f6,4d#6,8p,g6,g6,f6,4d#6";
static const char MELODY_40[] PROGMEM =
    "BeethovenElise:d=8,o=5,b=125:32p,e6,d#6,e6,d#6,e6,b,d6,c6,4a.,32p,c,e,a,4b.,32p,e,g#,b,4c.6,32p,e,e6,d#6,e6,"
    "d#6,e6,b,d6,c6,4a.,32p,c,e,a,4b.,32p,d,c6,b,2a";
// from http://www.vex.net/~lawrence/ringtones.html
static const char MELODY_41[] PROGMEM =
    "XFile:d=16,o=6,b=355:e.,p,e.,p,e.,p,e.,p,b.,p,b.,p,b.,p,b.,p,a.,p,a.,p,a.,p,a.,p,b.,p,b.,p,b.,p,b.,p,d.7,p,"
    "d.7,p,d.7,p,d.7,p,b.,p,b.,p,b.,p,b.,p,b.,p,b.,p,b.,p,b.,p,b.,p,b.,p,b.,p,b.,p";
static const char MELODY_42[] PROGMEM =
    "Fido:d=16,o=6,b=800:f,4p,f,4p,f,4p,f,4p,c,4p,c,4p,c,4p,c,1p,1p,1p,1p";
static const char MELODY_43[] PROGMEM =
    "Fido2:d=8,o=7,b=500:f,f6,f,f6,f,f6,c,c6,c,c6,c,c6,c,c6,c,c6,c,c6,c,c6,c,c6,c,c6,c,c6,c,c6,c,c6,c,c6,c,c6,c,2p";
static const char MELODY_44[] PROGMEM =
    "Intel:d=16,o=5,b=320:d,p,d,p,d,p,g,p,g,p,g,p,d,p,d,p,d,p,a,p,a,p,a,2p";
static const char MELODY_45[] PROGMEM =
    "Knock:d=32,o=4,b=100:e,4p,e,p,e,8p,e,4p,e,8p,e,4p";
// (old no. 46: same as no. 26, not here)
static const char MELODY_47[] PROGMEM =
    "Mosaic:d=8,o=6,b=400:c,e,g,e,c,g,e,g,c,g,c,e,c,g,e,g,e,c";
static const char MELODY_48[] PROGMEM =
    "Mosaic2:d=8,o=6,b=400:c,e,g,e,c,g,e,g,c,g,c,e,c,g,e,g,e,c,p,c5,e5,g5,e5,c5,g5,e5,g5,c5,g5,c5,e5,c5,g5,e5,g5,"
    "e5,c5";
static const char MELODY_49[] PROGMEM =
    "Triple:d=8,o=5,b=635:c,e,g,c,e,g,c,e,g,c6,e6,g6,c6,e6,g6,c6,e6,g6,c7,e7,g7,c7,e7,g7,c7,e7,g7";
// Short job sounds (about 1 s), made for this firmware
static const char MELODY_50[] PROGMEM =
    "PassTwoTone:d=8,o=6,b=180:g,2c7";
static const char MELODY_51[] PROGMEM =
    "PassTriad:d=8,o=6,b=240:c,e,g,2c7";
static const char MELODY_52[] PROGMEM =
    "PassFanfare:d=16,o=6,b=160:g,g,g,2c7";
static const char MELODY_53[] PROGMEM =
    "PassSparkle:d=16,o=7,b=125:b6,4e.";
static const char MELODY_54[] PROGMEM =
    "PassSteps:d=16,o=6,b=125:c,d,e,f,4g";
static const char MELODY_55[] PROGMEM =
    "PassFifth:d=8,o=6,b=200:c,p,2g";
static const char MELODY_56[] PROGMEM =
    "PassBounce:d=16,o=6,b=150:e,g,e,g,4c7";
static const char MELODY_57[] PROGMEM =
    "PassBell:d=16,o=7,b=125:c,e,g,4c8";
static const char MELODY_58[] PROGMEM =
    "PassLevel:d=16,o=6,b=150:g,c7,e7,8g7,e7,4g7";
static const char MELODY_59[] PROGMEM =
    "PassLift:d=16,o=5,b=150:c,e,g,c6,e6,4g6";
static const char MELODY_60[] PROGMEM =
    "FailTwoTone:d=8,o=5,b=180:c6,2f#";
static const char MELODY_61[] PROGMEM =
    "FailSlide:d=8,o=5,b=240:g,f#,f,2e";
static const char MELODY_62[] PROGMEM =
    "FailBuzz:d=16,o=4,b=150:c,p,c,p,4c";
static const char MELODY_63[] PROGMEM =
    "FailSteps:d=16,o=5,b=125:g,f,d#,d,4c";
static const char MELODY_64[] PROGMEM =
    "FailWahWah:d=8,o=5,b=240:d,c#,c,2b4";
static const char MELODY_65[] PROGMEM =
    "FailMinor:d=8,o=5,b=150:g,d#,4c";
static const char MELODY_66[] PROGMEM =
    "FailAlarm:d=16,o=6,b=150:e,c,e,c,e,c,e,c,8c";
static const char MELODY_67[] PROGMEM =
    "FailThud:d=8,o=4,b=180:d#,16p,2c";
static const char MELODY_68[] PROGMEM =
    "FailDrop:d=16,o=6,b=150:c7,g,d#,c,g5,4c5";
static const char MELODY_69[] PROGMEM =
    "FailUhOh:d=8,o=5,b=150:e,16p,4c#.";

static const char* const MELODIES[] = {
    MELODY_01, MELODY_02, MELODY_03, MELODY_04, MELODY_05, MELODY_06,
    MELODY_07, MELODY_08, MELODY_09, MELODY_10, MELODY_11, MELODY_12,
    MELODY_13, MELODY_14, MELODY_15, MELODY_16, MELODY_17, MELODY_18,
    MELODY_19, MELODY_20, MELODY_21, MELODY_22, MELODY_23, MELODY_24,
    MELODY_25, MELODY_26, MELODY_27, MELODY_28, MELODY_29, MELODY_30,
    MELODY_31, MELODY_32, MELODY_33, MELODY_34, MELODY_35, MELODY_36,
    MELODY_37, MELODY_38, MELODY_39, MELODY_40, MELODY_41, MELODY_42,
    MELODY_43, MELODY_44, MELODY_45, MELODY_47, MELODY_48, MELODY_49,
    MELODY_50, MELODY_51, MELODY_52, MELODY_53, MELODY_54, MELODY_55,
    MELODY_56, MELODY_57, MELODY_58, MELODY_59, MELODY_60, MELODY_61,
    MELODY_62, MELODY_63, MELODY_64, MELODY_65, MELODY_66, MELODY_67,
    MELODY_68, MELODY_69,
};

static constexpr int NUM_MELODIES = (int)(sizeof(MELODIES) / sizeof(MELODIES[0]));

int rtttlCount()
{
    return NUM_MELODIES;
}

const char* rtttlMelody(const int ix)
{
    return (((ix >= 0) && (ix < NUM_MELODIES)) ? MELODIES[ix] : nullptr);
}

void rtttlName(const int ix, char* name, const size_t size)
{
    if (size == 0) {
        return;
    }
    name[0] = '\0';
    const char* text = rtttlMelody(ix);
    if (text == nullptr) {
        return;
    }
    size_t len = 0;
    while ((len + 1) < size) {
        const char ch = (char)pgm_read_byte(text + len);
        if ((ch == '\0') || (ch == ':')) {
            break;
        }
        name[len] = ch;
        len++;
    }
    name[len] = '\0';
}

int rtttlSeconds(const int ix)
{
    Rtttl parser;
    if (!parser.begin(rtttlMelody(ix))) {
        return 0;
    }
    RtttlNote note;
    uint32_t ms = 0;
    while (parser.next(note, false)) {
        ms += note.ms;
    }
    return max((int)((ms + 500) / 1000), 1);
}

int rtttlFind(const char* name)
{
    char melodyName[24];
    for (int ix = 0; (name != nullptr) && (ix < NUM_MELODIES); ix++) {
        rtttlName(ix, melodyName, sizeof(melodyName));
        if (strcasecmp(melodyName, name) == 0) {
            return ix;
        }
    }
    return -1;
}

// =====================================================================================================================

/**
 * @brief Get next character (no white space, lower case), do not use it
 */
char Rtttl::peek()
{
    while (isspace((unsigned char)pgm_read_byte(pos))) {
        pos++;
    }
    return (char)tolower((unsigned char)pgm_read_byte(pos));
}

/**
 * @brief Get next character (no white space, lower case), use it
 */
char Rtttl::get()
{
    const char ch = peek();
    if (ch != '\0') {
        pos++;
    }
    return ch;
}

/**
 * @brief Read a number
 *
 * @returns the number, -1 if there is none
 */
long Rtttl::number()
{
    long num = -1;
    while (isdigit((unsigned char)peek())) {
        num = ((num < 0) ? 0 : (num * 10)) + (get() - '0');
        if (num > 100000) {
            num = 100000;
        }
    }
    return num;
}

/**
 * @brief Read value of a control pair, use it
 *
 * @param[in] name  Control name ("d", "o" or "b", others are ignored)
 */
void Rtttl::control(const char name)
{
    if (peek() == '=') {
        get();
    }
    const long value = number();
    switch (name) {
        case 'd':
            if ((value > 0) && (value <= 64)) {
                duration = (uint8_t)value;
            }
            break;
        case 'o':
            if ((value >= 1) && (value <= 8)) {
                octave = (uint8_t)value;
            }
            break;
        case 'b':
            if ((value > 0) && (value <= 1000)) {
                // b: quarter notes per minute
                wholeMs = (4 * 60 * 1000) / value;
            }
            break;
        default:
            break;
    }
}

bool Rtttl::begin(const char* text)
{
    pos = text;
    duration = 4;
    octave = 6;
    wholeMs = (4 * 60 * 1000) / 63;
    if (pos == nullptr) {
        return false;
    }

    // Name
    char ch = get();
    while ((ch != ':') && (ch != '\0')) {
        ch = get();
    }
    if (ch == '\0') {
        return false;
    }

    // Control section
    while (true) {
        ch = get();
        if (ch == ':') {
            return true;
        }
        if (ch == '\0') {
            return false;
        }
        if (ch != ',') {
            control(ch);
        }
    }
}

bool Rtttl::next(RtttlNote& note, const bool freq)
{
    // Semitones from C of each note letter a-g
    static const int8_t SEMITONES[7] = { 9, 11, 0, 2, 4, 5, 7 };
    while ((pos != nullptr) && (peek() != '\0')) {
        if (peek() == ',') {
            get();
            continue;
        }
        const long div = number();
        const char letter = get();

        // Control pair in the tone section
        if ((div < 0) && ((letter == 'd') || (letter == 'o') || (letter == 'b')) && (peek() == '=')) {
            control(letter);
            continue;
        }

        int semitone = -1;
        bool ok = true;
        if ((letter >= 'a') && (letter <= 'g')) {
            semitone = SEMITONES[letter - 'a'];
        } else if (letter != 'p') {
            ok = false;
        }
        if (peek() == '#') {
            get();
            semitone += ((semitone >= 0) ? 1 : 0);
        }
        bool dotted = false;
        if (peek() == '.') {
            get();
            dotted = true;
        }
        long oct = number();
        if (oct < 0) {
            oct = octave;
        }
        if (peek() == '.') {
            get();
            dotted = true;
        }

        // Junk up to the next note: skip this note
        while ((peek() != ',') && (peek() != '\0')) {
            get();
            ok = false;
        }
        if (!ok || (oct < 1) || (oct > 8) || (div == 0)) {
            continue;
        }

        uint32_t ms = wholeMs / ((div > 0) ? (uint32_t)div : duration);
        if (dotted) {
            ms += (ms / 2);
        }
        note.ms = (uint16_t)((ms > 60000) ? 60000 : ms);
        note.freq = 0;
        if (freq && (semitone >= 0)) {
            // B# = C of the next octave
            if (semitone >= 12) {
                semitone -= 12;
                oct++;
            }
            const float freq = 440.0f * powf(2.0f, (((float)(semitone - 9) / 12.0f) + (float)(oct - 4)));
            note.freq = (uint16_t)(freq + 0.5f);
        }
        return true;
    }
    return false;
}
