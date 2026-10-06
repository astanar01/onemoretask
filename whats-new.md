# What's new

## 2026-10-06

- When Claude stops because its safety check keeps failing, the card now says so and the board gets Claude going again by itself after a minute. Before, the card just said "stopped" and waited for you.

## 2026-10-05

- When Claude asks if it may do something, you can now answer right on the card: Allow, Allow always or Deny. No need to open a terminal anymore.
- Helpers that Claude sent off and that already came back now show as finished. Before, they all looked "running" again each time you sent Claude a new message, even though they were doing nothing.

## 2026-10-04

- When Claude stops to ask if it may do something, the card now says "Claude asks permission" and shows what it wants to do (even right after Claude said it was done), with a button to copy the command that lets you answer. An old question you already answered no longer shows as a new one.

## 2026-10-02

- Claude no longer slips a task into a hidden copy of your project on its own. If your computer was set up that way, your changes seemed to vanish, the Run app button was missing, and the app started without them. Now only the board makes a copy, and only when the card's box is ticked.
- The "Use worktrees" box left the gear menu. Each card now has its own "Use a worktree" box, ticked for new cards. Untick it and Claude works right in your project folder. The choice is saved on the card, so an older window can no longer send a task the wrong way. If your folder has no git repo, the box that pops up now has a button to send the task without a worktree.
- The project list now drops down under the project name instead of covering it, so you can still see where you are.

## 2026-10-01

- The Subagents list is now short: each helper takes one line. Click a helper to see what it did last and the skills it used, and click again to fold it back. A helper that is still working also shows what it is doing now.
- When a task is split between helpers and you answer Claude's question, Claude now hands the new work to helpers again instead of doing it all alone.
- When a task is split between helpers, Claude now picks the right helpers' guides for each part and tells them to read those first, so each helper works the way the project wants.
- Each helper in the Subagents list now shows the skills it used, so you can see what each one is following.
- The "Next action" box now always shows "Merge worktree to main" and "Review code" side by side. You no longer have to read the changes or wait for a check before you can merge. To read the changes, open the Changes tab.
- When you press "Run app" on a website task, Claude now opens it in a new tab for you. Web addresses Claude writes on a card can now be clicked too, and open in a new tab.
- In the Changes tab, clicking an open change again now closes it. Before, it just opened the same thing again.
- A card in Done whose changes are not in your project yet now has a "Merge worktree to main" button in its "Next action" box. Before, the button never showed there.
- The board now notices when another window changed it (a card moved, a note typed) and shows the change within a few seconds, so you no longer need to reload the page. If you change something in an old window, the board reloads itself and asks you to do that change again, instead of putting the old board back. Sending a card to Claude from an old window does not start Claude; the board asks you to send again. The last change you make before closing the page is kept.
- While Claude is working, a small arrow turns in a circle next to "Work in progress" and on the card's "Claude: working" label, so you can see it is still busy and not stuck. It sits in the middle of the words and grows with the text size.
- When you send a message to a task that is done, it is not done anymore: it moves back to In Progress while Claude works on it.
- An open card has a new look. The left side shows the task's name, its column and priority, and a "Next action" box that tells you what to do now: wait, look at the changes, bring them in, or mark the task done. The big button there is the one to press. Under it, small fold-out rows hold the technical bits, the helpers Claude used and the words it was given.
- The right side of an open card now has four tabs: Summary (what Claude did, what changed and whether it is in your project yet), Changes (the files and saved changes, click one to see the difference), Conversation (all messages, with the reply box at the bottom) and Images. Finished tasks open on Summary, busy ones on Conversation.
- The "Next action" box looks at your project for real: it counts files that are changed and saved changes that are not in your main copy yet, so "nothing to merge" is only said when it is true. If it cannot look, it says so instead of showing zero.
- The orange note that a reply will cost more because Claude's memory went cold now sits right above the reply box and says how much it will reload.
- The row of buttons at the bottom of a card is gone. Deleting the task or its separate copy is under the "⋯" menu at the top right; the "Review code", "Run app" and "Merge" buttons moved into the Next action box and the Changes tab.
- The top bar is tidier: theme, text size and "Show subtasks" are under "View"; "Use worktrees" and the Changelog are under the gear button.

- When the board starts again, each card fills in as soon as it is ready, open cards first, instead of all of them staying empty until the very end.
- A small box at the top of the board shows which copy of the board you are using. It turns orange when you are on a task's separate copy instead of your main one, and you can pick another copy there to jump to it. The board will not throw away the copy it is running from: go back to main first. The top bar now stays on one line.
- While "Delete worktree" is working, the whole board turns grey and shows "Deleting the worktree…", so you cannot click anything by mistake until it is finished.
- If you write to Claude on a card after its separate copy was thrown away or brought into your project, Claude now gets a fresh separate copy to work in, so your own copy stays as it is.
- The "Claude finished" and "Claude needs you" pop-ups now wait until the card has moved or shows the change, so you no longer look at the board before it is ready.

## 2026-09-30

- A card with its own separate copy of your project now has a "Delete worktree" button, in any column. It throws that copy away without bringing its changes in. If the copy holds work you would lose, a small window shows it and asks first.
- An open card now shows a "Skills used" box on the left: the special helpers Claude picked up while working on it, and how many times it used each one.
- Moving a card to Done no longer adds it to a list of changes in your project. Nothing on the board used that list.
- There is a new "Use worktrees" box at the top, under "Show subtasks on board". When it is ticked, Claude does each task in its own separate copy of your project, so your own copy stays as it is. When Claude is finished, the card has two new buttons: "Run app from worktree" to try the changes, and "Merge worktree to main" to bring them into your project. If something goes wrong when bringing them in, Claude tells you on the card. If your project does not keep its history yet, a small window asks if the board should set that up; your tasks and your secret ".env" settings are kept out of it. A card you move to Done before bringing its changes in now tells you so.
- In a new card, the model picker is only as wide as the model's name, and "Send to Claude" stays on one line.
- In an open card, the left side is now narrower (a quarter of the width), so Claude's messages on the right get more room.
- A card you move to Done now shows at the top of the Done column, not at the bottom, so the newest finished work is first.
- When you move a card to Done, its message box is greyed out until Claude's summary of the work shows on the card.

## 2026-09-29

- When you move a card to Done, Claude writes a short recap of what was decided, why, how and what came out, and shows it on the card. Then it closes that card's Claude work so it stops piling up and taking space. If you write on the card again later, a fresh Claude starts from that recap. A message you send while the recap is being made is kept and given to that fresh Claude.
- When another project needs you, an orange star shows inside the project list, even while you look at a different project. Open the list to see which one.
- The small check mark at the top of each column is gone. Only the Done column counts as done now, so Move to done always lands in Done.

## 2026-09-28

- The Observations window has a new Run full review button. It makes a card where Claude looks at all the notes and asks you which ones to use.
- There is a new Changelog button at the top. It shows what changed in the board.
- You can grab a card and move it up or down to change its order.
- There is a new Update button at the top. It turns green when a newer board is ready, and one click gets it. It stays hidden when you already have the newest one. On Windows, the board now always comes back after an update.
- The Review code button only shows up when Claude changed something on the card.
- There is a new "Answer only" box. Claude looks into your question and answers on the card, without changing anything.
- Notes you type on a card now reach Claude right away.
- The list of messages on a card no longer goes blank while Claude is writing.
- New cards remember your last choice of Claude and of helpers.
- When you move a card to Done, it is added to your project's list of changes.
- Messages on cards look nicer, with bold words, headings, and dot lists. Problems found in a review get small colored badges.
- Setting up the board now makes a shortcut you can double-click to start it.
- The board has its own little picture in the browser tab.
- The board is safer. Other websites can no longer talk to it.

## 2026-09-27

- You can send Claude a note on a card while it works. The note goes straight to Claude.
- The list of messages on a card no longer jumps to the bottom while you scroll up to read. It follows new messages again after you send one.
- Each card shows a row for every helper that is running.
- A card says "Code review" while Claude is checking the work.
- The Review code check costs less now. It tries out every problem it finds to prove it is real.
- The project menu marks projects that have work waiting for you, with a star and a number.
- There is a new Observations button. It shows tips Claude wrote down to get better at its work, and you can apply them.
- Claude now answers in your language.
- The board now works on Windows computers too.
- Cards no longer slide into other columns by mistake.
- The card window and the Observations window now start below the top bar. The card title sits on the left side.
- When something goes wrong, the card window shows the problem above its buttons.
- The Review code, depth, and Move to done buttons have more space between them.

## 2026-09-26

- There is a new Review code button in the Review column. Claude checks the work on that card for mistakes.

## 2026-09-24

- You can pick how the board looks: like your computer, light, or dark.
- You can set up the board and start it with one short word.
- If a project folder is not trusted yet, the board asks you in a box in the middle of the screen instead of failing.
- A card that Claude works on shows a short summary on the left side of its window. If helpers are working on it, you see a list of them.
- Cards that just finished go on top of the Review column.
- When Claude is stuck waiting, the board tells you why instead of guessing.
- The Move to done button hides while Claude still has work to do on the card.

## 2026-09-23

- You can paste pictures into cards and replies. Claude can show pictures on the card too.
- You can open any project, not just one.
- The card window is bigger and fills the screen, with two sides. It has an X button to close it.
- Click a picture to see it big.
- A text size menu lets you make the words bigger or smaller.
- The + Add task button opens the full card window.
- Cards in Review that you have not read yet stand out.
- Each card has a colored dot that shows how important it is.
- You can pick which Claude to use when you send a card. The picker names the usual one.
- You can ask Claude to split a big job into smaller parts, with helpers for each part.
- When you reply on a card, Claude always answers on the card.
- New cards show up at the top of their column.
- A card is locked once you send it to Claude, so it does not change while Claude works.
- Cards move to Review when Claude is done, even if Claude looked stuck.
- Your message shows up right away with a little spinning wheel, then a check mark. The reply box is taller and hides once you send.
- The Send to Claude and Delete buttons moved to the left side of the card window.

## 2026-09-22

- There is a new board with columns of cards. A card can hold smaller cards, and you can fold them away.
- A Send to Claude button lets Claude work on a card. The card moves to In Progress right away.
- Claude writes what it did on the card, and tells you when it needs you.
