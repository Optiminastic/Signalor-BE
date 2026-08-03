"""Slack integration.

Split by responsibility so each piece can be tested on its own:

* ``blocks``  - pure formatting, no I/O. An AnalysisRun in, Block Kit out.
* ``client``  - the Slack Web API and nothing else.
* ``notify``  - subscribes to the ``analysis.completed`` event and joins the two.

The analyzer never imports any of this. It already emits ``analysis.completed``
(see apps/public_api/signals.py); Slack listens. Adding Teams or Discord later
is a new folder beside this one, with no change to the analysis pipeline.
"""
