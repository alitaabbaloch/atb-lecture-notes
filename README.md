<p align="center">
  <img src="extension/icons/icon128.png" width="96" height="96" alt="ATB Lecture Notes logo">
</p>

# ATB Lecture Notes

Watch a lesson in your browser. Get it in writing.

ATB Lecture Notes records the sound of the lesson playing in your browser tab and writes down what was said, on your own Mac. It is made for lessons in Urdu or Hindi mixed with English, and it writes Roman Urdu (Urdu in English letters).

> **Works on:** a Mac with an Apple chip (M1 or newer), macOS 14 or newer, Google Chrome.
>
> **Not yet:** Windows, and Macs with an Intel chip.

## What you get after each lesson

- **A transcript** with a time stamp on every line, so you can jump to that spot in the video.
- **A for-AI file:** the transcript plus ready-made instructions. Give it to any AI chat (ChatGPT, Claude, Gemini) and it writes your study notes.
- **The sound recording.**

Your lessons stay on your Mac. The tool sends no lesson sound and no lesson text to any online service.

## Install it: the easy way

An AI walks you through it. You do not need to know anything technical.

1. **Download the tool.** Click the green **Code** button at the top of this page, then **Download ZIP**. Or use this link: [Download ZIP](https://github.com/alitaabbaloch/atb-lecture-notes/archive/refs/heads/main.zip).
2. **Open the zip.** Double-click it in your Downloads folder. You get a folder named `atb-lecture-notes-main`.
3. **Find the guide.** Inside that folder is a file named `AI-SETUP-GUIDE.md`.
4. **Give the guide to an AI.** Open ChatGPT, Claude, Gemini or any other AI chat. Attach the file, or open it, copy everything and paste it. Then write:

   > Help me install and use ATB Lecture Notes. Follow this guide.

5. **Follow it, one step at a time.** It checks your Mac, runs the installer with you, adds the extension to Chrome and does a first test with you.

Set aside 20 to 60 minutes. Most of it is waiting for a download of about 2 GB.

If something goes wrong, tell the AI exactly what you see on the screen. The guide tells it what each message means.

## Install it yourself: the short version

For people who are comfortable with Terminal. Every step is explained in more detail in [AI-SETUP-GUIDE.md](AI-SETUP-GUIDE.md).

1. Check your Mac: Apple menu > About This Mac. You need "Chip: Apple M..." and macOS 14 or newer.
2. Open Chrome once, if you never have.
3. Download the ZIP (see above), unpack it, rename the folder to `atb-lecture-notes` and move it into your home folder.
4. Open Terminal and run:

   ```bash
   zsh ~/atb-lecture-notes/install/install-mac.command
   ```

   It downloads about 2 GB and takes a while. It worked when it ends with `Installed. Three steps left in Chrome`.
5. In Chrome, open `chrome://extensions`, turn on **Developer mode**, click **Load unpacked** and choose the folder `atb-lecture-notes/extension`. Then pin the extension.

## Use it

1. Open your lesson in Chrome and play it.
2. Click the **ATB Lecture Notes** icon.
3. Check the three fields:
   - **course:** the name of the whole course. Pick it from the list after the first time.
   - **lesson:** the title of this lesson.
   - **speed:** the speed your video is playing at.
4. Click **Record this tab**. You still hear the lesson as usual.
5. At the end, click **Stop and transcribe**.
6. Wait until the lesson shows `done`. A 45 minute lesson takes a few minutes.

Your files are in **Documents > ATB Lecture Notes > notes**, in a folder named after the course.

To get study notes, give the file that ends in `-for-ai.md` to any AI chat.

## Good to know

- **It is a machine listening.** Names, numbers and English tool names can come out wrong. Check anything important against the video. The time stamps make that quick.
- **The spelling is Hindi-style Roman Urdu,** for example "nahin" and "vah".
- **It does not say who is speaking.**
- **Match the speed.** If the video plays at 1.5x and the panel says 1x, the transcript gets worse.
- **You can teach it spellings.** Each course folder has a `glossary.txt` where you list a term and the wrong spellings the tool produced.
- **Record only what you are allowed to record.** That is your responsibility.
- **This is an early release.** It has been tried on very few Macs. If something breaks, please report it under **Issues** at the top of this page. Leave lesson text and personal details out.

## Your privacy

- The sound, the transcript and the for-AI file stay on your Mac.
- The extension does not read what is on the lesson page. It notes the tab's title and web address, and it saves the address at the top of the transcript. Delete that line before you share a transcript with someone.
- The internet is used only to download the tool's own parts: the packages and the speech model during the install, and a version check of the second model if you switch on the optional second hearing.

## Optional extras

Both are described in [AI-SETUP-GUIDE.md](AI-SETUP-GUIDE.md).

- **A second hearing.** A second speech model listens to every lesson again and fills holes. It needs 3 GB more.
- **Automatic notes.** If you have your own API key from Anthropic or DeepSeek, the tool can write the notes itself. This sends the transcript text, never the sound, to that company.

## Remove it

Run `zsh ~/atb-lecture-notes/install/uninstall-mac.command` in Terminal, remove the extension in `chrome://extensions`, and delete the `atb-lecture-notes` folder. Your lessons in Documents are not touched.

## Credits and licence

- ATB Lecture Notes is released under the [MIT licence](LICENSE).
- The Roman Urdu speech model is [Whisper-Hindi2Hinglish-Apex](https://huggingface.co/Oriserve/Whisper-Hindi2Hinglish-Apex) by Oriserve (Apache-2.0). It is downloaded from its author's page during the install.
- The optional second hearing uses Whisper large-v3 by OpenAI (MIT), in the MLX format from the MLX community.
- It runs on Apple's MLX through the mlx-whisper package.
