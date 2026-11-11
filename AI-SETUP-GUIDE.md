# ATB Lecture Notes: setup guide for your AI assistant

**If you are the person:** give this whole file to an AI assistant (Claude, ChatGPT, Gemini, Codex or another) and say: "Help me install and use ATB Lecture Notes. Follow this guide." Then do what it asks, one step at a time. You do not need to read the rest.

**If you are the assistant:** everything below is for you.

## How to use this file

This file is context, not a script to recite. It tells you what the tool is, how it is installed, what the person will see, and what usually goes wrong. Use your own judgment for anything it does not cover. Only the short list under "Firm rules" is fixed.

The quick path: check the Mac, put the folder in place, run the installer, add the extension to Chrome, do a one minute test, then record a real lesson.

## Who you are helping

A student who wants written transcripts of lessons they watch in their browser. Assume they are capable but not technical. They may never have opened Terminal. They may write in English, Urdu or Roman Urdu: answer in the language they use.

## How to guide them

- **One step at a time.** Give one step, say what they should see when it worked, and wait for their answer before the next step.
- **If you can run commands on their Mac** (you are an agent with a terminal, such as Claude Code or Codex): run them yourself. Before each command, say in one plain sentence what it does and what it changes. Ask the person to do only what you cannot do: clicking in Chrome, answering a macOS question.
- **If you can only chat:** give exact steps, one action per step. Put each command in its own code block. They paste it into Terminal with Cmd+V and press Enter. To show you what Terminal printed, they select the text, copy it with Cmd+C and paste it into the chat. The lines after the last `==>` are usually enough.
- **Use full paths.** Never say "the config file". In this guide `~` means the person's home folder. Explain that once.
- **When something fails,** say what the message means and the one next thing to try. Do not list five possibilities.
- **Plain words.** Explain a technical word in a few words the first time, and do not lecture.
- **One question at a time,** and only when you cannot find the answer yourself.

## Firm rules

1. **Stay away from the lesson website.** Never open, fetch, log in to, click on, read or automate the site where the person watches lessons, and never ask for their login. The person opens and plays lessons themselves. Your work is on their Mac and in the extension's panel. The tool itself does not touch that page: it records the tab's sound and notes the tab's title and address, nothing else. Keep it that way.
2. **No secrets in the chat.** Never ask them to paste a password, an API key, or the contents of `secrets.env` or `admin.token`. If a key is needed (only for the optional notes add-on), they type it into the file themselves.
3. **Ask before any download over 1 GB,** and say the size first. The installer downloads about 2 GB. The optional second hearing downloads 3 GB.
4. **Do not delete or move their lessons** (the `notes` and `recordings` folders), and do not run the uninstaller, unless they ask.
5. **Mac only, for now.** If the computer is not a Mac with an Apple chip and macOS 14 or newer, stop and say so. There is no Windows or Intel version yet. Do not try workarounds. A Mac with an Apple chip and an older macOS can be updated, and then it works.
6. **Recording is their call.** Recording a lesson is the person's own decision, and it is on them to record only what they are allowed to record. You never start or stop a recording for them.

## What the tool is

ATB Lecture Notes records the sound of one browser tab and turns it into a written transcript on the person's own Mac.

- **A Chrome extension** records the tab's sound. It does not read what is on the page. It does read the tab's title, to suggest a lesson name, and the tab's address, which is saved at the top of the transcript. Its only connection is to this same computer (`127.0.0.1`, port 8765).
- **A small program on the Mac** (called the server) receives the sound and writes the transcript. The extension starts it when needed.
- **A speech model** (1.6 GB, downloaded once) does the listening. It is made for Urdu or Hindi speech mixed with English, and it writes Roman Urdu: Urdu in English letters, with Hindi-style spelling such as "nahin" and "vah".
- **Lessons stay on the Mac.** The tool sends no lesson sound and no lesson text to any online service. It uses the internet only to fetch its own parts: the packages and the speech model during the install, and a version check with the model's download site each time the optional second hearing runs.
- **A for-AI file** is written for every lesson: the transcript plus instructions, ready to hand to any chat AI to get study notes.

Two extras are optional and not needed to start: a second hearing by a second speech model, and an add-on that writes notes with the person's own API key. Both are described near the end.

## What they need

| Need | How to check |
|---|---|
| A Mac with an Apple chip (M1 or newer) | Apple menu > About This Mac. "Chip: Apple M..." is right. "Processor: Intel" is not supported. In Terminal, `uname -m` prints `arm64`. |
| macOS 14 (Sonoma) or newer | Same window. In Terminal, `sw_vers -productVersion` prints 14 or higher. |
| Google Chrome, opened at least once | Brave, Edge and Chromium also work. Safari and Firefox do not. |
| About 5 GB of free disk space | About 2.5 GB stays in use after the install. |
| Internet for the install | About 2 GB of downloads in total. The speech model is 1.6 GB of that. |
| 20 to 60 minutes | Mostly waiting for downloads. |
| The program folder `atb-lecture-notes` | From the project's page, `https://github.com/alitaabbaloch/atb-lecture-notes`: green **Code** button, then **Download ZIP**. Or a folder or zip from whoever shared this guide. This file is inside it. |

## Install, step by step

### Step 1: check the Mac

Check the first two rows of the table. If either fails, stop (firm rule 5).

### Step 2: put the folder where it will stay

1. If they have a zip (from GitHub it is named `atb-lecture-notes-main.zip`), they double-click it to unpack it.
2. The folder they need is the one that holds the folders `install`, `extension` and `server`. If unpacking made a folder inside a folder, it is the inner one.
3. If it has another name, such as `atb-lecture-notes-main`, they rename it to `atb-lecture-notes`.
4. They drag it into their home folder (in Finder: Go > Home). Its path is then `~/atb-lecture-notes`.

It worked when `~/atb-lecture-notes` holds the folders `install`, `extension` and `server`.

Another place is fine too, but then use that path in every command below. Dragging a folder onto the Terminal window types its path. Two things to know: the folder must not be moved or renamed after installing (if it is, run the installer again), and its path must not contain a quote or a backslash.

### Step 3: run the installer

Tell them first that it downloads about 2 GB, and get a yes (firm rule 3). Then, in Terminal (press Cmd+Space, type Terminal, press Enter):

```bash
zsh ~/atb-lecture-notes/install/install-mac.command
```

Use this command and not a double-click: macOS blocks double-clicking a script that came from a zip.

What it does, in order (each stage prints a line that starts with `==>`):

1. Checks that this is a Mac with an Apple chip.
2. Installs `uv`, a tool that manages Python, if it is missing (with Homebrew when the Mac has it, otherwise with uv's own installer). uv fetches its own copy of Python.
3. Installs the Python packages into a `.venv` folder inside the program folder.
4. Creates the app folder `~/Library/Application Support/ATB Lecture Notes` with default settings.
5. Writes a start helper and registers it with each browser it finds, so the extension can start the server.
6. Downloads the speech model (1.6 GB) and converts it.
7. Prints three steps to do in Chrome.

Things to tell them before they start:

- It can sit for several minutes without printing anything new while it downloads. That is normal.
- The Mac should stay awake and plugged in. If it sleeps and the download stops, they run the same command again.
- It should not ask for a password. If something asks for one, stop and look at what is asking.
- macOS may ask whether Terminal may access a folder. The answer is Allow.

It worked when both are true:

- The stage `==> Telling your browsers about the helper` printed a line that starts with `Registered for` and names their browser, for example `Registered for Google/Chrome`.
- The last block starts with `==> Installed. Three steps left in Chrome`.

It is safe to run again. It keeps existing settings, and a broken download carries on where it stopped.

If it stops, it prints `Stopped:` and the reason:

| Message | Meaning and next step |
|---|---|
| `this installer needs an Apple Silicon Mac` | Not supported. Stop. |
| `uv was installed but is not on the PATH` | Open a new Terminal window and run the same command again. |
| `uv sync failed` | Scroll up for the error. Usually there is no internet, or macOS is older than 14. In the second case the error names the package `mlx`, usually together with `macosx_14_0`. Updating macOS fixes it. |
| `the model download failed` | Check the internet connection and run the command again. It continues, it does not start over. |
| Any other `Stopped:` message | It says in its own words what failed. Running the same command again is safe and is often enough. |

One line is not a stop but matters: `No Chromium browser folder found`. The installer still ends with `==> Installed.`, but the extension will not be able to start the server. Chrome is missing or was never opened. They install it, open it once, and run the installer again.

### Step 4: add the extension to Chrome

The person does this in Chrome:

1. Type `chrome://extensions` in the address bar and press Enter. (In Brave it is `brave://extensions`, in Edge `edge://extensions`.)
2. Turn on **Developer mode** (switch at the top right).
3. Click **Load unpacked** (top left) and choose the folder `extension` inside the program folder. In the file window, Cmd+Shift+G lets them paste the path `~/atb-lecture-notes/extension`.
4. Click the puzzle icon in Chrome's toolbar, then the pin next to **ATB Lecture Notes**, so the icon stays visible.

It worked when a card named "ATB Lecture Notes 1.0.0" appears with no red Errors button. Its ID is `flkffpbhlmeolggahhancjnpcjnoggaa` on every computer.

Chrome may show a note about developer mode. That is expected: the extension is loaded from a folder, not from Chrome's store.

### Step 5: a one minute test

1. They open any page with someone talking (a short video is fine) and play it.
2. They click the ATB Lecture Notes icon. A small panel opens. It has a server button at the top right, the fields **course**, **lesson** and **speed**, a button **Record this tab**, and a list called **queue** at the bottom.
3. The lesson field already holds the tab's title. They replace it with `First test`, type `Test` in course, and set speed to the speed the video is playing at (normally `1x`).
4. They click **Record this tab**. The server button goes from `[ server off ]` to `[ starting ]` to `[ server on ]`. The first start can take 20 seconds. A timer runs, and the sound keeps playing as usual.
5. After about a minute they click **Stop and transcribe**.
6. Under queue the lesson shows `transcribing`, then `done` and the path of the file.

While the server starts, the panel may say `Server is off. Audio is being kept in the browser and will upload when the server starts.` That is normal and nothing is lost.

It worked when this file exists and holds the spoken words: `~/Documents/ATB Lecture Notes/notes/Test/01-First-test.md`. In Finder: Documents > ATB Lecture Notes > notes > Test. They can open it with TextEdit (right-click > Open With > TextEdit).

macOS may ask once whether Chrome may access the Documents folder. The answer is Allow.

### Step 6: a real lesson

The person opens the lesson and plays it as usual. Then, in the panel:

- **course**: the name of the whole course. They type it the first time and pick it from the list after that. Spelled differently, it becomes a second course. The field shows the last course used.
- **lesson**: the title of this one lesson. The field is filled in with the tab's title, so they check it and correct it.
- **speed**: the speed the video is playing at, from `1x` to `2x`. The tool slows the sound back to normal before it listens, so a wrong value gives a worse transcript. One speed for the whole lesson. The panel remembers the speed of each course.

The common mistake is swapping course and lesson. Check both fields with them before they press Record. The fields lock while it records.

Then **Record this tab** at the start and **Stop and transcribe** at the end. While it records:

- The panel can close. Recording goes on. They click the icon again to open it and stop.
- They can use other tabs and windows. The lesson tab must stay open and Chrome must stay running.
- Pausing the video is fine. Silence is removed from the transcript.

The queue shows where a lesson is: `recording`, `queued`, `transcribing 40%`, `done`, or `failed` with a Retry button. A 45 minute lesson takes roughly 3 to 5 minutes to transcribe on a recent Mac. They should wait for `done` before shutting the Mac.

**If a recording is cut short** (Chrome quits, the tab closes, the Mac sleeps): what was recorded up to then is kept. After a minute the panel offers a button **Finish now**, which transcribes what arrived. That is the sure way. Left alone, the tool finishes the lesson by itself after 5 minutes and the queue shows `(recovered)`. If the server was restarted in between, it waits much longer, up to 12 hours.

**If course and lesson were swapped:** nothing is lost. Only the folder and file names are wrong. There is no rename feature yet. They should not rename or move the files by hand: the tool keeps its own list of lessons and would lose track of this one. If they want well named files, they copy them.

## What each lesson leaves behind

Everything is in `~/Documents/ATB Lecture Notes/`:

| File | What it is |
|---|---|
| `notes/<course>/01-<lesson>.md` | The transcript, with a time stamp on every line. |
| `notes/<course>/01-<lesson>-for-ai.md` | The transcript plus instructions for a chat AI. |
| `notes/<course>/01-<lesson>.json` | The same transcript with technical details. The tool uses it. |
| `notes/<course>/glossary.txt` | The spelling list of this course. |
| `recordings/<course>/<date>-<time>-<lesson>.webm` | The sound recording. |
| `notes/<course>/01-<lesson>.second.json` | Only with the second hearing: what the second model heard. |
| `notes/<course>/01-<lesson>-notes.md` | Only with the notes add-on: the notes. |

- In these names, `<course>` and `<lesson>` are the names typed in the panel, with spaces turned into hyphens and punctuation removed.
- Lessons of a course are numbered 01, 02, 03 as their transcripts are written.
- The transcript starts with a block of details: course, lesson, the lesson's web address, date, length and speed. If they share a transcript with someone, they may want to delete the address line first.
- In the text, `[unclear until 12:40]` marks a stretch the model could not transcribe, and `(?)` marks a word it could not make out.
- The file ends with the same words again as one paragraph, under `## Plain text`.

## Study notes from the transcript, with any chat AI

No API key is needed for this.

1. They open a chat with any AI and attach the `-for-ai.md` file, or paste its text.
2. The file starts with instructions for that AI, so no other prompt is needed.
3. A long lesson is cut into parts that start with `=== PART`. If the chat says the text is too long, they paste one part at a time.

If you are that chat AI and they hand you the file, follow the instructions inside it.

Tell them plainly: the transcript is made by a machine. Names, numbers and English tool names can be wrong, and the notes inherit those mistakes. Every note carries a time stamp so they can check it in the video.

## Fixing spellings: the glossary

Each course gets a `notes/<course>/glossary.txt` when its first transcript is written. It comes filled with a starter list of common terms. They edit it with TextEdit and save. No restart is needed.

Examples of lines:

```
WordPress = word press | ورڈ پریس
Claude = clod | clot
Next.js
```

Left of `=` is the right spelling. Right of it are the wrong spellings the transcript produced, separated by `|`. A line with no `=` only tells a notes AI how the term is spelled.

- It applies to every transcript written after the change, in that course.
- A transcript that already exists is not changed. For that one, Find and Replace in the `.md` file is the way.
- Add a wrong spelling only when it is never a real word in a lesson: "clod" is safe, "cloud" is not.

## Optional: a second hearing

A second speech model (Whisper large-v3, 3 GB) transcribes every lesson again in the background. It fills holes in the transcript, and it is added to the for-AI file, where it helps the chat AI get names and numbers right. The transcript is ready as fast as before. The second hearing follows later: about 8 minutes for a 45 minute lesson on the one Mac it was measured on. Other Macs can take two or three times as long.

After a yes to the 3 GB download:

```bash
zsh ~/atb-lecture-notes/install/get-second-model.command
```

Then the server must restart: in the panel they click `[ server on ]`, then `[ stop server? ]` within 4 seconds. It starts again on the next recording. If the panel says `The server is busy with a recording or a transcription. Stop it when that has finished.`, they wait for that and try again.

## Optional: notes written automatically, with their own API key

Only for someone who has an API key from Anthropic or DeepSeek. It costs a few cents per lesson, and it sends the transcript text (never the sound) to that company.

1. Install the add-on's packages:

```bash
cd ~/atb-lecture-notes && uv sync --no-dev --group notes
```

2. They put the key into the key file themselves (firm rule 2). This opens it in TextEdit:

```bash
touch ~/Library/Application\ Support/ATB\ Lecture\ Notes/secrets.env && open -e ~/Library/Application\ Support/ATB\ Lecture\ Notes/secrets.env
```

   One line per key, then save: `ANTHROPIC_API_KEY=...` or `DEEPSEEK_API_KEY=...` or both. With both, Claude writes the notes and DeepSeek checks them.

3. Make notes for one lesson (about 2 to 5 minutes):

```bash
~/atb-lecture-notes/.venv/bin/python ~/atb-lecture-notes/addons/notes/make_notes.py "/Users/<their name>/Documents/ATB Lecture Notes/notes/<course>/01-<lesson>.md"
```

   The path in quotes must be the full path that starts with `/Users/`. `~` does not work inside quotes. It writes `01-<lesson>-notes.md` next to the transcript.

To get notes after every lesson without asking, set `"auto_notes": true` in `~/Library/Application Support/ATB Lecture Notes/config.json` and restart the server. With the second hearing also on, the notes are written after the second hearing has finished.

Running the installer again removes the add-on's packages. Repeat step 1 after that.

## When something goes wrong

Messages in the extension's panel:

| Message | Meaning and next step |
|---|---|
| `Start helper not installed. Run install-mac.command once.` | The installer was not run, or it was run before Chrome was opened for the first time. Run the installer again with the `zsh` command from step 3 (not a double-click), then click the server button. |
| `The server did not start. Press the server button to try again.` | Try once more. It also happens when the program folder was moved or renamed after installing: then run the installer again. Otherwise read the end of `server.log` (see below). |
| `An older server is running on this port. Close it, then press the server button.` | Another program, or an old copy of this tool, is using port 8765. They quit it, or restart the Mac, then click the server button. |
| `Server is off. Audio is being kept in the browser and will upload when the server starts.` | Normal for some seconds while the server starts. If it stays, they click the server button. Nothing is lost. |
| `This tab cannot be recorded. Open the lesson page and try again.` | Chrome's own pages cannot be recorded. They click the icon while the lesson tab is in front. |
| `Recording did not start. Reload the tab, then try again.` | They reload the lesson tab and record again. |
| `No sound from this tab for 21 s` (the number counts up) | Appears after 20 seconds of silence. The video is paused, or the tab is muted. Recording itself is fine. |
| `The recording stopped by itself. Open the lesson tab and record again.` | The lesson tab was closed, or it stopped giving sound. What was recorded is kept. They record again from where it stopped. |
| `No audio is arriving for this lesson.` | The recording was cut short. They press **Finish now** to transcribe what arrived. |
| `Transcription failed. Press Retry to try again.` | They press Retry. If it fails again, read `server.log`. |
| `The server is busy with a recording or a transcription. Stop it when that has finished.` | The server refuses to stop while it works. They wait and try again. |
| `The extension did not answer. Reload it from the extensions page.` | On `chrome://extensions` they click the round arrow on the ATB Lecture Notes card. |

Other messages say what to do in their own words.

Where to look:

- Logs: `~/Library/Application Support/ATB Lecture Notes/logs/` holds `server.log`, `host.log` and `extension.log`. The last 30 lines of `server.log` usually name the problem. Look for `ERROR`, `Traceback` or `WARNING`.
- Is the server on? `curl -s http://127.0.0.1:8765/health` prints `"ok":true` when it is.
- Start the server by hand, if the extension cannot: `zsh ~/atb-lecture-notes/start-server.command`. It runs as long as that Terminal window stays open, and it prints its messages in that window instead of `server.log`.

If the transcript is poor: check that the speed in the panel matched the video, add wrongly spelled terms to the glossary, and consider the second hearing.

## Settings, updating, removing

- **Settings** are in `~/Library/Application Support/ATB Lecture Notes/config.json`. The ones worth knowing: `data_dir` (where lessons are saved; `null` means `~/Documents/ATB Lecture Notes`), `second_hearing`, `auto_notes`. Restart the server after a change. Leave `port` alone: the extension only talks to port 8765.
- **Updating:** replace the folder `~/atb-lecture-notes` with the new one, run the installer again, and click the round arrow on the extension's card in `chrome://extensions`. Settings, the speech model and all lessons are kept: they live outside that folder.
- **Removing:** `zsh ~/atb-lecture-notes/install/uninstall-mac.command`, then Remove on the extension's card, then delete the program folder. The uninstaller asks whether to delete the app folder. A yes removes the settings, the tool's list of lessons, the key file and the 1.6 GB speech model. It never deletes the lessons in Documents. The optional 3 GB model stays in `~/.cache/huggingface/hub` until they delete it themselves.

## Limits to be honest about

- This is an early release, tried on very few Macs. Something in this guide may not match what they see. When that happens, say so and work from what is on their screen.
- The transcript is made by a machine. The Roman spelling is Hindi-style.
- There are no speaker labels: the text does not say who is talking.
- It is made for Urdu or Hindi mixed with English. Other languages are outside this guide.
- If you are stuck after a few tries, help them write the short report below. They send it to whoever shared the tool, or post it under **Issues** on the project's GitHub page.

## At the end: a short report

If they are one of the first users, offer to write a short report they can send to whoever shared the tool, or post under **Issues** on the project's GitHub page:

- Mac model and macOS version.
- How long the install took, and where it stopped, with the exact message.
- Whether the one minute test and the first real lesson worked.
- How the transcript reads to them.

Leave lesson text, web addresses and personal details out of the report.
