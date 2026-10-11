# Spec schema

Fill every field from the brief. Never invent a value. Where the brief is silent, use null and add an
entry to `open_questions`. Every object that holds a rule carries `src`: the rule number(s) or a short
quote of the brief text it came from. Dates are ISO (YYYY-MM-DD), times HH:MM, weekdays 1=Monday ... 7=Sunday.
When later material (a memo, an email reply, an amendment) changes an earlier rule, the spec holds the
final value and `overrides` records what changed.

The example values below come from one sample brief. They only show the shape. Take every value from
the brief you are given.

```jsonc
{
  "bank_name": "Freya Bank",
  "language": "English",
  "agent_identity": "digital assistant calling from Freya Bank",
  "call_variables": [{"name": "customer_id", "meaning": "pass to every tool"}],
  "now_variable": "now",

  "opening": {
    "first_sentence_must_say": ["digital assistant calling from Freya Bank", "the call is recorded"],
    "confirm_question": "Am I speaking with {title} {last_name}?",
    "accepted_confirmations": ["yes", "that's me", "speaking", "their own name"],
    "not_confirmations": ["hello", "go ahead", "who is this"],
    "max_asks": 2,
    "unclear_result": {"outcome": "not_reached", "reason": "unclear"},
    "src": "rules 1-3"
  },
  "forbidden_before_verification": ["collections", "debt", "payment", "product names"],

  "third_party": {
    "say_nothing_beyond_opening": true,
    "ask_when_reachable": true,
    "callback_window": {"days": "today_or_next_business_day", "from": "10:00", "to": "19:00",
                        "inclusive": true, "must_be_after_now": true},
    "tool": "schedule_callback",
    "asks_outside_window": 1,
    "outcomes": {
      "callback":     {"outcome": "not_reached", "reason": "callback"},
      "no_callback":  {"outcome": "not_reached", "reason": "no_callback"},
      "wrong_number": {"outcome": "not_reached", "reason": "wrong_number"},
      "deceased":     {"outcome": "not_reached", "reason": "deceased", "say": "I am sorry for your loss.", "ask_nothing_else": true}
    },
    "third_party_may_not": ["answer security questions", "hear account information", "pay"],
    "src": "rules 5-10"
  },

  "identity": {
    "tool": "verify_identity",
    "questions": [
      {"field": "date_of_birth", "ask": "date of birth", "tool_format": "YYYY-MM-DD",
       "parse_rule": "a date given only as numbers is day, month, year"},
      {"field": "phone_last4", "ask": "last four digits of the phone number on file", "tool_format": "four digits"}
    ],
    "one_at_a_time": true,
    "max_wrong_total": 3,
    "hints": [{"hint": "day_month_swapped", "action": "ask them to say the month as a word", "counts_as_wrong": false}],
    "on_failed":   {"say": "use the mobile app or a branch", "outcome": "not_verified", "reason": "failed"},
    "on_declined": {"explain_once": "the questions protect their account", "outcome": "not_verified", "reason": "declined",
                    "counts_as_declining": ["asking what the call is about instead of answering"], "closes_on": "second refusal"},
    "src": "rules 11-15"
  },

  "account": {
    "tool": "get_account",
    "statement_must_include": ["calling from the collections team about a late payment", "product in words", "past-due amount", "days late"],
    "product_names": {"credit_card": "credit card ending {card_last4}", "personal_loan": "personal loan", "car_loan": "car loan"},
    "src": "rules 16-17"
  },

  "calendar": {"business_weekdays": [1, 2, 3, 4, 5], "holidays": ["2026-10-26"], "src": "section 7"},

  "bands": [
    {"name": "early", "field": "days_late", "min": 1, "max": 7,
     "deadline_business_days_after_today": 12, "persuasion_attempts": 1, "src": "rule 18"}
  ],

  "promise_date": {
    "earliest": "today",
    "must_be_business_day": true,
    "latest": "deadline",
    "non_business_day_not_after_deadline": "offer the next business day",
    "after_deadline": "say the latest date is the deadline and offer it; refusing it is a flat refusal",
    "vague_period": "ask once for the exact day; if none, propose the deadline",
    "relative_weekdays": {"next <weekday>": "that weekday in the following calendar week",
                          "this <weekday>": "that weekday in the current week"},
    "src": "rules 19-23"
  },

  "amounts": {
    "first_ask": "full past-due amount",
    "agent_proposes_every_amount": true,
    "floor_field": {"credit_card": "minimum_payment", "personal_loan": "instalment_amount", "car_loan": "instalment_amount"},
    "full_too_much": "offer the floor once and say the rest stays overdue; refusing the floor is a flat refusal",
    "customer_amount_between_floor_and_full": "accept",
    "customer_amount_below_floor": "say it cannot be recorded and offer the floor once; not accepting is a flat refusal",
    "src": "rules 24-27"
  },

  "negotiation": {
    "first_question": "Can you make this payment today?",
    "customer_proposal_is_never_refusal": true,
    "flat_refusal_definition": "a no to paying today with no date and no amount, or a non-answer",
    "attempts": [
      {"n": 1, "bands": ["early", "late"], "amount": "full, or the floor if the customer already said full is too much",
       "date": "last business day of this week; if today is that day, last business day of next week"},
      {"n": 2, "bands": ["late"], "amount": "floor", "date": "deadline"}
    ],
    // Every date the agent must PROPOSE by itself (attempt dates, default dates), as a named rule
    // relative to today and the product's deadline. Copy the brief's wording exactly.
    "derived_dates": [
      {"name": "attempt1_date", "rule": "last business day of this week; if today is that day, last business day of next week"},
      {"name": "attempt2_date", "rule": "the product's deadline"}
    ],
    "never_repeat_refused_offer": true,
    "when_attempts_used_up": {"outcome": "no_promise", "reason": "refused"},
    "never_offer": ["discount", "waiver", "instalment plan", "any term not in the brief"],
    "src": "rules 28-33"
  },

  "confirmation": {
    "read_back": ["product", "amount", "date"],
    "question": "Do you confirm?",
    "record_only_after_clear_yes": true,
    "change_at_read_back": "check the new value again and read back again",
    "one_promise_per_product": true,
    "change_after_recorded": "say they can change it in the mobile app",
    "tool": "create_promise",
    "success_field": "ok",
    "on_failure": {"retries": 1, "then_say": "could not record it", "outcome": "no_promise", "reason": "service_error"},
    "after_success_say": "You will receive a text message confirming this promise.",
    "src": "rules 34-38"
  },

  "intents": [
    {"name": "already_paid", "trigger": "says they already paid this past-due amount",
     "before_verification": "not applicable", "say": "payments can take one business day to appear",
     "take_promise": false, "tool_calls": [], "outcome": "paid", "reason": null,
     "precedence": "", "src": "rule 39"},
    {"name": "restructuring", "trigger": "asks for a new payment plan or lower instalments",
     "say": "do not describe a plan", "tool_calls": [{"tool": "transfer_to_agent", "args": {"queue": "restructuring"}}],
     "outcome": "transferred", "reason": "restructuring", "src": "rule 41"}
  ],

  "multi_product": null,

  "closing": {
    "tool": "close_call",
    "exactly_once_and_last": true,
    "before_close": "thank the customer and say goodbye",
    "outcomes": [{"outcome": "promise", "reason": null, "when": "at least one promise was recorded",
                  "stages": ["after_verification"]}],
    // stages: where this outcome can happen. Any of "before_confirmation" (nobody confirmed being the
    // customer yet, or someone else answered), "during_identity" (customer confirmed, security questions
    // not all passed), "after_verification". Include every stage where the brief allows it or could
    // allow it (a rule that says "before identity is verified" covers before_confirmation too).
    "special": ["if a transfer follows a recorded promise, close as transferred with that transfer's reason"],
    "src": "rules 46-47"
  },

  "tool_usage": {"never_used": ["add_note"], "notes": ""},
  "other_rules": [{"text": "", "src": ""}],
  "overrides": [{"what": "", "old": "", "new": "", "src": ""}],
  "open_questions": []
}
```

When customers can have more than one product, `multi_product` is an object instead of null:

```jsonc
{
  "max_products": 3,
  "state_every_product_first": true,
  "ask_all_today_first": true,
  "product_order": "as returned by get_account",
  "attempts_counted_per_product": true,
  "start_each_product_at_attempt": 1,
  "one_date_after_a_products_deadline": "record that product on its own deadline and say so in the read-back",
  "single_read_back_and_confirmation": true,
  "note_tool": "add_note",
  "note_each_product_without_promise": true,
  "note_reasons": ["refused", "hardship", "dispute", "paid", "service_error"],
  "dispute_or_paid_does_not_end_call": true,
  "on_transfer": "promises recorded stay; agreed but unrecorded are not recorded; no notes at all",
  "failed_promise": "keep recorded promises, note service_error for the failed product",
  "closing_rule": "no promise: all paid -> paid; else no_promise with the reason of the first non-paid note in get_account order; promise wins except on transfer",
  "src": "brief B"
}
```
