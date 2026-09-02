MO2 Fight Log
=============

Reads Mortal Online 2's combat log out of a gameplay recording and turns it
into a report you can actually read. MO2 never writes a combat log to disk --
it only draws it on screen -- so the numbers are read off the video frames.

HOW TO USE IT
-------------
Drag a video file onto MO2FightLog.exe and let go.

A black window opens and works for roughly 90 seconds per 2 minutes of
footage. When it finishes, a report opens in your browser. The report and a
.json of the raw data are saved next to your video, named after it.

Nothing to install. Python and ffmpeg are already inside this folder.

BEFORE YOU RECORD
-----------------
Leave the chat panel on the Combat tab. Everything here is read out of that
panel, so a clip recorded with the panel showing Guild or Party chat has
nothing in it to read, however good the fight was. The floating damage text in
the middle of the screen is not the same thing and is not used.

WINDOWS ONLY
------------
The text recognition uses the engine built into Windows, which is why there is
nothing to install. It will not run on a Mac or on Linux.

WHAT TO EXPECT
--------------
Checked against fights counted by hand, it reads the damage exactly on clean
1080p footage with the combat log unobstructed. Heavier compression, a smaller
UI scale or a very busy log will all read worse, and it errs toward dropping a
doubtful reading rather than guessing at it -- so if it is wrong, it is more
likely to have missed a hit than invented one.

Each line of the log sits on screen for several seconds and is read from every
frame in that span, so a number is settled by what the frames agree on rather
than by any one of them. That is what stops a single bad frame turning a 55
into a 551.

THE REPORT
----------
A single self-contained file. You can email it or post it and it will open
anywhere, no internet needed.

Drag either handle under the chart to zoom into part of the fight, and click
the eye beside a name to take them out of the count -- the chart and every
number redraw for what is left. Click one of the parry or counter stats to
mark those moments on the chart, and hover a mark for the time it happened.

IF IT GOES WRONG
----------------
The window stays open and prints what happened.

If the report comes back with nothing in it, the chat panel was probably not
on the Combat tab -- see above.

Otherwise the usual cause is the combat log sitting somewhere other than the
bottom-left of the screen, so nothing was found. That happens with unusual
resolutions or UI scaling.
