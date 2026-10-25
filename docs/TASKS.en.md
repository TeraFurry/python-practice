# How to load and make tasks

**English** · [Русский](TASKS.ru.md)

A task set is one `.json` file: a title, a help panel and a list of questions. The app reads it and checks the learner's solutions by itself.

## 1. How to load a task

1. In the app window, bottom left, press "Ещё" (More), then "Добавить задания из файла" (Add tasks from a file) and choose a `.json` file. You can pick several files at once.
2. Or put the file into the tasks folder and restart the app. When you run from source this is the `Задания/` folder in the repository. For the exe it is `Documents/Практика Python/Задания/`.

The file name (without `.json`) may contain only letters, digits, space, `_` and `-`. If a task with that name already exists, the app will not overwrite it and tells you so. If the file has an error, the app shows what is wrong (for example "задача 3: нет текста условия", i.e. question 3 has no text).

A ready example to start from: [`example-task.json`](example-task.json).

## 2. How to ask an AI assistant to write tasks

If you are not a programmer, copy the prompt below into any AI assistant, replace the words in `[brackets]` and save the answer as a file with the `.json` extension. Then load it as described above. If the app reports an error, send the error text back to the assistant and ask it to fix the file.

```
Write a beginner Python practice task as a single JSON file for the app "Python Practice".
Topic: [for example "the for loop"]. Language of the texts: [English / Russian]. I need [7] questions.

File format:
- Top level: "title", "topic", "general" (a short "how to answer" note shared by the whole task), "help" (a list of help steps: {"h": heading, "t": text, "code": code example}), "questions" (a list of questions).
- The first help item is a "Lesson" that explains the topic in simple words with examples.
- Each question: {"type": "script", "text": the task text, "about": "Practice: ...", "before": ready code BEFORE the solution (optional), "after": ready code AFTER the solution (usually print(...)), "starter": code that is already in the field (for "fix the ready code" questions), "expected_output": the exact console output, "assigns": [variable names that must be created], "uses_names": [variable names that must be used], "consts": {name: the value that must be assigned to it}, "required": [operators + - * / // % ** that must appear in the solution], "required_text": [fragments that must appear in the code], "max_text": {fragment: how many times it may be written}, "expected_error": "name of the error, e.g. NameError (instead of expected_output)", "inputs": [answers to input(), in order]}. Use only the fields a question needs.
- In texts you can use `code`, **bold** and ==highlight==. Each task text has three lines: "**What to do:** ...", "**Expected result:** ...", "**Where to look:** ...".

Rules for the questions:
1. A ladder: first 1-2 "repeat the example from the lesson", then 1-2 "fix the ready code", then "write your own", and one question about reading an error at the end.
2. The task text says what to do and what the result must be, but never hints HOW. Hints live in the help.
3. No hard math and no word problems with a story. Numbers are small and round; Python does the counting.
4. Simple words. Every new term is explained once.
5. Check that the correct solution of every question really prints expected_output.
Return only the JSON.
```

## 3. File structure

### Top level

| Field | Required | Meaning |
|---|---|---|
| `title` | yes | name of the task set (shown in the list and in the header) |
| `topic` | no | topic, a small label above the title |
| `general` | no | shared "how to answer" text (tab "О задании"), one per task set |
| `help` | no | the help tab: a list of steps `{"h": heading, "t": text, "code": example}` |
| `questions` | yes | the list of questions (not empty) |

### Question of type `script` (the main type)

The app glues `before` + the code from the field + `after`, runs it like a normal `.py` file and compares the output with `expected_output`.

| Field | What it does |
|---|---|
| `type` | `"script"` |
| `text` | the task text (required) |
| `about` | a short line in the "О задании" tab ("Practice: ...") |
| `before`, `after` | ready code before and after. In the window they are grey with a lock and cannot be edited |
| `starter` | code that is already in the field (for "fix the ready code" questions) |
| `placeholder` | grey hint in an empty field |
| `min_lines` | how many rows the field shows (1-30, default 3) |
| `inputs` | list of answers to `input()`, in order |
| `expected_output` | the exact console output (trailing spaces and empty lines at the edges are ignored) |
| `expected_error` | instead of output: the name of the error the code must raise, e.g. `"NameError"` or `"TypeError"` |
| `required_text` | list of fragments that must be in the code |
| `max_text` | `{"fragment": N}`: the fragment may be written at most N times (for example, so the result cannot be typed by hand) |
| `assigns` | list of variables that must be created (`name = ...`) |
| `consts` | `{"name": value}`: this exact value must be assigned to the variable as a literal (number without quotes, text in quotes) |
| `min_assigns` | `{"name": N}`: the variable must be assigned at least N times |
| `uses_names` | list of variables that must take part in the calculation instead of being typed as a ready value |
| `required` | operators from `+ - * / // % **` that must appear in the solution |

The extra types `expression`, `text`, `code` and `shell` are kept for old files; new tasks do not need them.

### Text markup

In `text`, `t` and `general` you can write `` `code` ``, `**bold**`, `__underline__` and `==highlight==`. Every line break is a new line on screen. Labels at the start of a line (`Что сделать:`, `Что должно получиться:`, `Где смотреть:`, `Что дано:`, `Ограничение:` and so on) are highlighted automatically; for other languages just write the label in bold, like `**What to do:**`.

In a help step's `code`: a line that starts with `|` is shown as a ready (locked) part, a line that starts with `→` is shown as program output, and `# ...` is a comment.

## 4. How to write good tasks

This is a short recipe the three tasks in this repository were made with. You do not have to follow it: if you like hard tasks, write them your own way; the file format stays the same.

1. **One topic at a time.** Take the pages of the book or the documentation for exactly this topic and check terms and examples against them.
2. **Lesson first.** The first help item: what it is in one simple sentence, examples straight from the book, and what the error message means if the topic has one. Write in paragraphs and simple words. Explain a book term once.
3. **Help steps.** Short and numbered, one idea per step. Code examples have comments and an arrow `→` with the result.
4. **A ladder of 6-8 questions.** 1-2 "repeat the example from the lesson by hand", 1-2 "fix the ready code" (the `starter` field), then "write your own" from scratch, then a question where the learner reads an error message, and one that combines everything.
5. **Three lines per task text:** "What to do", "Expected result", "Where to look" (the Help tab, "Lesson" and step numbers). The text says what is needed but never how. Put the rules of the task (what is not allowed) directly into "What to do".
6. **Easy numbers.** No hard math and no word problems with a story. Numbers are small and round; Python counts. Tasks about text, names, letters, reports and errors work best.
7. **Close the shortcuts.** So the learner cannot just type the ready answer, use `required`, `required_text`, `max_text`, `uses_names`, `consts`, `min_assigns`.
8. **Check before handing out.** The correct solution of every question passes, a wrong one does not. Step numbers in "Where to look" match the help. Every example in the help works when typed from scratch.

This follows what research on teaching programming to beginners recommends: fully worked examples first, then "complete" and "fix" tasks, then writing your own, with the help removed gradually.
