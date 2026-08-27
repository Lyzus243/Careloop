# Fixed follow-up schedule — not user configurable
FOLLOWUP_RULES = {
    "new_customer_followup_days": 14,      # Follow up after 14 days
    "new_customer_max_followups": 1,        # Stop after 1 automatic follow-up
    "existing_customer_followup_days": 7,   # Follow up every 7 days
    "existing_customer_max_followups": 3,   # Max 3 follow-ups per purchase cycle
    "overdue_grace_days": 3,               # Days after due before marked overdue
}
