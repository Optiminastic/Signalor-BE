"""Subject line A/B/C variants per email step.

Variants are picked uniformly at random per send, then attached to the
Amplitude `email_sent` event so subject-line lift can be measured downstream.
Subjects use Django template syntax — render with the same context as the body.
"""

SUBJECT_VARIANTS = {
    1: {
        "A": "Your GEO score for {{ domain|default:'your site' }} is ready",
        "B": "You're one step from tracking AI citations",
        "C": "Finish setting up Signalor for {{ domain|default:'your site' }}",
    },
    2: {
        "A": "Questions before you go live with Signalor?",
        "B": "How Signalor pricing actually works",
        "C": "4 things people ask before activating",
    },
    3: {
        "A": "Your Signalor workspace for {{ domain|default:'your site' }} is configured",
        "B": "{% if issue_count %}{{ issue_count }} fixable issues{% else %}Fixable issues{% endif %} detected on {{ domain|default:'your site' }}",
        "C": "{% if competitor_count %}{{ competitor_count }} competitors tracked — ready when you are{% else %}Your competitors are tracked — ready when you are{% endif %}",
    },
    4: {
        "A": "quick question, {{ first_name|default:'there' }}",
        "B": "quick question, {{ first_name|default:'there' }}",
        "C": "quick question, {{ first_name|default:'there' }}",
    },
}
