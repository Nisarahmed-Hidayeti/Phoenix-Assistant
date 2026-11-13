# Phoenix Assistant

**A local AI assistant that actually works with your computer.**

Python · Open Source · Local AI · Privacy First

> **Status:** Early development. This repository is where I'm documenting the project as I build it.

---

## The idea

I've always wanted to build my own AI assistant for a long time.

Not just another chatbot that answers questions. I wanted something that could actually interact with my computer, help me get things done, and make everyday tasks a little easier.

Something that feels like it's part of my operating system instead of just another website I have to open.

That's where **Phoenix** comes in.

The idea is pretty simple: build a personal desktop assistant that runs locally, can eventually interact with applications and files, and doesn't need to send everything to a cloud server.

I know that's a pretty ambitious project, especially building it from scratch. But that's also what makes it interesting.

## Why local?

I like having control over my own computer.

And I don't really like the idea of sending my files, conversations, or personal information to external servers just to use an assistant.

So one of my biggest goals with Phoenix is to keep as much as possible on the user's own machine.

That means aiming for:

- Local AI processing whenever possible
- No unnecessary cloud dependencies
- Control over what the assistant can access
- Transparency about what it's doing
- No unnecessary background services or bloat

I'm not against online AI services. I just don't want them to be a requirement for everything.

---

## What I want Phoenix to do

This is the direction I'm exploring, not a list of completed features.

### 1. Understand natural language

I want to communicate with my computer without having to memorize hundreds of commands.

For example, being able to ask Phoenix to find something, open an application, or help with a repetitive task.

### 2. Actually control the PC

This is probably the part I'm most interested in.

Instead of just giving instructions, Phoenix should eventually be able to perform useful actions.

Opening applications, interacting with files, managing simple tasks, and automating things I would otherwise have to do manually.

Obviously, that also means designing proper permission controls. I don't want an AI randomly deleting my files.

### 3. Work locally

The goal is to support local language models so Phoenix can perform its core tasks without needing a constant internet connection.

I haven't decided on the final model or inference setup yet.

### 4. Remember useful context

I'd like Phoenix to eventually have an optional memory system.

Not to record everything I do, but to remember information I explicitly allow it to keep, so I don't have to explain the same things repeatedly.

And I want users to be able to see and delete that information.

### 5. Stay simple

I don't want to turn this into a massive application filled with features nobody uses.

If a feature makes the assistant genuinely more useful, great.

If it just adds complexity, I'd rather leave it out.

---

## How I'm planning to build it

Python is going to be the main language.

I'm still researching the rest of the architecture, especially the local AI models, operating-system integrations, and how to give the assistant access to tools safely.

One thing I want to avoid is building everything at once.

I'd rather start with something small that actually works, then gradually expand it.

---

## A personal note

Phoenix is one of those projects I've wanted to make for a long time.

I don't know exactly how far I'll be able to take it yet.

But I want to build something that I would genuinely use on my own computer, not just something that looks impressive in a demo.

Even if it takes a while.

*— November 2026*

---

## Try the first working version

Phoenix runs a native Qt chat window on Linux with a local llama.cpp model. The first demo finds and files a PDF invoice, writes a verified summary, and opens the destination in Dolphin. A user-confirmed Kate workflow uses KDE's desktop portal to append and save a line.

See [setup, demo, and machine results](DEVELOPING.md).
