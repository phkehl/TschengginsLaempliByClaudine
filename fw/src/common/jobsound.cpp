// Job sounds: melody or sound effect when a job stops
//
// The LED config on the backend has the sounds of each LED (see backend/laempli/leds.py), the state of each LED has
// them: {"fail": <sound>, ...}. Sound: a melody name (rtttl.hpp, any case) or "fx" (sound effect, only the Hello has
// one). Events:
//
// - fail:   the job stopped with FAIL or WARN
// - pass:   the job stopped with PASS
// - broken: the job stopped with FAIL or WARN, the result before was PASS (instead of fail, if set)
// - fixed:  the job stopped with PASS, the result before was FAIL or WARN (instead of pass, if set)
//
// The result before is the one of the old state: the result of the last finished run (also while the job runs).
//
// Job stopped: the new status is STOPPED, the job (source, path) is the same as before, and
//
// - the status before was RUNNING or WAITING, or
// - the job has a new time (a new update), and the status before was UNKNOWN (e.g. timed out), or the result changed
//   (a run without RUNNING state)
//
// Generic jobs: the same. A script sets RUNNING at the start and STOPPED with the result at the end. An update without
// RUNNING before plays only if the result changes (so info-only updates play no sound).
//
// So the first state after the start plays no sound, and a new job for an LED plays no sound. A state with more
// events plays one sound: the most important event (JobSound order), the lowest LED first.

#include <Arduino.h>

#include "config.hpp"
#include "jobsound.hpp"
#include "melody.hpp"
#include "rtttl.hpp"

// Event keys, in JobSound order
static const char* const EVENT_KEYS[JOB_SOUNDS] = { "broken", "fail", "fixed", "pass" };

static constexpr const char* FX_NAME = "fx";

int8_t jobSoundFind(const char* name)
{
    if (name[0] == '\0') {
        return SOUND_NONE;
    }
    if (strcasecmp(name, FX_NAME) == 0) {
        return SOUND_FX;
    }
    const int ix = rtttlFind(name);
    return ((ix >= 0) ? (int8_t)ix : SOUND_NONE);
}

void jobSoundParse(JsonObjectConst json, int8_t* sounds)
{
    for (int ev = 0; ev < JOB_SOUNDS; ev++) {
        sounds[ev] = jobSoundFind(json[EVENT_KEYS[ev]] | "");
    }
}

/**
 * @brief Check if a job stopped (see above)
 */
static bool jobStopped(const LedInfo& before, const LedInfo& now)
{
    if (!before.used || !now.used || (now.status != LedStatus::STOPPED) || (strcmp(before.source, now.source) != 0) ||
        (strcmp(before.path, now.path) != 0)) {
        return false;
    }
    if ((before.status == LedStatus::RUNNING) || (before.status == LedStatus::WAITING)) {
        return true;
    }
    const bool newTime = (now.time != before.time);
    return (newTime && ((before.status == LedStatus::UNKNOWN) || (before.result != now.result)));
}

/**
 * @brief Get the event of an LED
 *
 * @returns the event (index in JobSound order) with a sound, -1 if none
 */
static int jobEvent(const LedInfo& before, const LedInfo& now)
{
    if (!jobStopped(before, now)) {
        return -1;
    }
    const bool wasPass = (before.result == LedResult::PASS);
    const bool wasBad = ((before.result == LedResult::FAIL) || (before.result == LedResult::WARN));
    JobSound first;
    JobSound second;
    if ((now.result == LedResult::FAIL) || (now.result == LedResult::WARN)) {
        first = (wasPass ? JobSound::BROKEN : JobSound::FAIL);
        second = JobSound::FAIL;
    } else if (now.result == LedResult::PASS) {
        first = (wasBad ? JobSound::FIXED : JobSound::PASS);
        second = JobSound::PASS;
    } else {
        return -1;
    }
    // Broken and fixed: fail and pass if not set
    for (const JobSound ev : { first, second }) {
        if (now.sounds[(int)ev] != SOUND_NONE) {
            return (int)ev;
        }
    }
    return -1;
}

int8_t jobSoundSelect(const LedInfo* before, const LedInfo* now, const int num)
{
    int best = JOB_SOUNDS;
    int bestIx = -1;
    for (int ix = 0; ix < num; ix++) {
        const int ev = jobEvent(before[ix], now[ix]);
        if ((ev >= 0) && (ev < best)) {
            best = ev;
            bestIx = ix;
        }
    }
    if (bestIx < 0) {
        return SOUND_NONE;
    }
    const int8_t sound = now[bestIx].sounds[best];
    char name[24];
    jobSoundName(sound, name, sizeof(name));
    Serial.printf("Sound: LED %d %s: %s\r\n", bestIx + 1, EVENT_KEYS[best], name);
    return sound;
}

void jobSoundPlay(const int8_t sound)
{
    if (sound == SOUND_FX) {
        if (!soundFxPlay()) {
            Serial.println("Sound: no sound effect");
        }
    } else if (sound >= 0) {
        melodyPlay(sound);
    }
}

void jobSoundOptions(JsonObject obj)
{
    // No speaker: empty list
    JsonArray melodies = obj["melodies"].to<JsonArray>();
    char name[24];
    char item[32];
    for (int ix = 0; (SPEAKER_PIN >= 0) && (ix < rtttlCount()); ix++) {
        rtttlName(ix, name, sizeof(name));
        snprintf(item, sizeof(item), "%s:%d", name, rtttlSeconds(ix));
        melodies.add(item);
    }
}

void jobSoundName(const int8_t sound, char* name, const size_t size)
{
    if (sound == SOUND_FX) {
        strlcpy(name, FX_NAME, size);
    } else if (sound >= 0) {
        rtttlName(sound, name, size);
    } else if (size > 0) {
        name[0] = '\0';
    }
}
