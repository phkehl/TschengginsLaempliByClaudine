// Stägge shell commands: LEDs (see shellled.cpp; shell.cpp for the common commands)

#include <Arduino.h>

#include "shell.hpp"
#include "shellled.hpp"

void shellExtHelp()
{
    shellLedHelp();
}

bool shellExtCommand(const char* cmd, const char* sub, const char* arg1, const char* arg2, const char* arg3)
{
    if (strcmp(cmd, "led") == 0) {
        shellLedCommand(sub, arg1, arg2, arg3);
        return true;
    }
    return false;
}

void shellExtStatus()
{
    shellLedStatus();
}

void shellExtConfig()
{
    shellLedConfig();
}
