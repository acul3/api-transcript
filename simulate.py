"""
Simulate real workload: read mtsamples.csv, transform medical notes into
doctor-patient transcripts, POST them to the API, then benchmark results.

Usage:
    python simulate.py                          # 3 samples, sequential
    python simulate.py --count 10 --concurrent  # 10 samples, concurrent
    python simulate.py --count 5 --concurrent --patch  # also test PATCH
"""

import asyncio
import csv
import os
import random
import re
import time
import argparse
import httpx

BASE_URL = "http://localhost:8000"
CSV_PATH = os.getenv("CSV_PATH", "mtsamples.csv")

# ── Section → speaker mapping ──────────────────────────────────────────────
PATIENT_SECTIONS = {
    "SUBJECTIVE", "HISTORY OF PRESENT ILLNESS", "HPI", "CHIEF COMPLAINT",
    "CC", "PAST MEDICAL HISTORY", "PMH", "SOCIAL HISTORY", "SHX",
    "FAMILY HISTORY", "FHX", "MEDICATIONS", "CURRENT MEDICATIONS",
    "ALLERGIES", "REVIEW OF SYSTEMS", "ROS",
    "MISCELLANEOUS/EATING HISTORY",
}

DOCTOR_SECTIONS = {
    "OBJECTIVE", "PHYSICAL EXAMINATION", "EXAM", "ASSESSMENT", "PLAN",
    "IMPRESSION", "IMPRESSION/PLAN", "FINDINGS", "DESCRIPTION",
    "FINDINGS AND PROCEDURE", "PROCEDURE IN DETAIL", "OPERATIVE PROCEDURE",
    "PROCEDURE", "DOPPLER", "SUMMARY", "COURSE",
    "PREOPERATIVE DIAGNOSIS", "POSTOPERATIVE DIAGNOSIS",
    "INDICATION FOR PROCEDURE", "INDICATIONS FOR PROCEDURE",
    "INDICATION FOR OPERATION", "ANESTHESIA",
}

SECTION_PATTERN = re.compile(
    r"([A-Z][A-Z /\-\(\)]{2,})\s*:\s*,?\s*", re.MULTILINE
)

# ── Natural phrasing templates ─────────────────────────────────────────────
_PATIENT_OPENERS = {
    "CHIEF COMPLAINT": [
        "The main reason I'm here is {body}.",
        "I came in because {body}.",
        "What's been bothering me is {body}.",
    ],
    "CC": [
        "The main reason I'm here is {body}.",
        "I came in because {body}.",
    ],
    "HISTORY OF PRESENT ILLNESS": [
        "So what happened was, {body}.",
        "Let me explain what's been going on. {body}.",
        "It started like this — {body}.",
    ],
    "HPI": [
        "Here's what's been happening. {body}.",
        "Let me walk you through it. {body}.",
    ],
    "SUBJECTIVE": [
        "Well, here's how I've been feeling. {body}.",
        "From my side, {body}.",
    ],
    "PAST MEDICAL HISTORY": [
        "In terms of my medical history, {body}.",
        "Previously I've had {body}.",
        "My past medical history includes {body}.",
    ],
    "PMH": [
        "My medical history — {body}.",
        "In the past I've dealt with {body}.",
    ],
    "SOCIAL HISTORY": [
        "About my lifestyle, {body}.",
        "On the personal side, {body}.",
    ],
    "SHX": [
        "Personally, {body}.",
        "As for my lifestyle, {body}.",
    ],
    "FAMILY HISTORY": [
        "In my family, {body}.",
        "As for family medical history, {body}.",
        "On my family's side, {body}.",
    ],
    "FHX": [
        "Family-wise, {body}.",
        "In my family, {body}.",
    ],
    "MEDICATIONS": [
        "I'm currently taking {body}.",
        "My medications are {body}.",
        "Right now I'm on {body}.",
    ],
    "CURRENT MEDICATIONS": [
        "Currently I take {body}.",
        "I'm on {body}.",
    ],
    "ALLERGIES": [
        "As for allergies, {body}.",
        "I should mention my allergies: {body}.",
        "Allergy-wise, {body}.",
    ],
    "REVIEW OF SYSTEMS": [
        "Other symptoms I've noticed — {body}.",
        "Going through everything else, {body}.",
    ],
    "ROS": [
        "As for other symptoms, {body}.",
        "Besides that, {body}.",
    ],
    "MISCELLANEOUS/EATING HISTORY": [
        "About my eating habits, {body}.",
        "Diet-wise, {body}.",
    ],
}

_DOCTOR_OPENERS = {
    "PHYSICAL EXAMINATION": [
        "Let me go through the exam findings. {body}.",
        "On examination, {body}.",
        "I've completed the physical exam. {body}.",
    ],
    "EXAM": [
        "On examination I found {body}.",
        "The exam shows {body}.",
    ],
    "OBJECTIVE": [
        "Looking at the clinical findings, {body}.",
        "Here's what I observed. {body}.",
    ],
    "ASSESSMENT": [
        "Based on everything, my assessment is {body}.",
        "Putting it all together, {body}.",
        "My clinical impression is {body}.",
    ],
    "PLAN": [
        "Here's what I'd like us to do. {body}.",
        "For the plan going forward, {body}.",
        "So the next steps are {body}.",
    ],
    "IMPRESSION/PLAN": [
        "My impression and plan is as follows. {body}.",
        "Here's my assessment and what we'll do next. {body}.",
    ],
    "IMPRESSION": [
        "My impression is {body}.",
        "Based on my evaluation, {body}.",
    ],
    "FINDINGS": [
        "The findings show {body}.",
        "What I found is {body}.",
    ],
    "PROCEDURE IN DETAIL": [
        "Let me walk you through the procedure. {body}.",
        "Here's how the procedure went. {body}.",
    ],
    "OPERATIVE PROCEDURE": [
        "Regarding the surgery, {body}.",
        "The operative details are as follows. {body}.",
    ],
    "PROCEDURE": [
        "The procedure performed was {body}.",
        "We carried out the following. {body}.",
    ],
    "FINDINGS AND PROCEDURE": [
        "Let me describe what we found and what we did. {body}.",
        "During the procedure, {body}.",
    ],
    "PREOPERATIVE DIAGNOSIS": [
        "Before the procedure, the diagnosis was {body}.",
        "Going in, we identified {body}.",
    ],
    "POSTOPERATIVE DIAGNOSIS": [
        "After the procedure, the confirmed diagnosis is {body}.",
        "Post-operatively, the diagnosis stands as {body}.",
    ],
    "INDICATION FOR PROCEDURE": [
        "The reason we're doing this procedure is {body}.",
        "This is indicated because {body}.",
    ],
    "INDICATIONS FOR PROCEDURE": [
        "The indications for this procedure are {body}.",
        "We're proceeding because {body}.",
    ],
    "INDICATION FOR OPERATION": [
        "The reason for this operation is {body}.",
    ],
    "ANESTHESIA": [
        "For anesthesia, we used {body}.",
        "The patient was put under with {body}.",
    ],
    "DOPPLER": [
        "The Doppler study shows {body}.",
        "On Doppler imaging, {body}.",
    ],
    "SUMMARY": [
        "To summarize, {body}.",
        "In summary, {body}.",
    ],
    "COURSE": [
        "Over the course of care, {body}.",
        "The clinical course was as follows. {body}.",
    ],
    "DESCRIPTION": [
        "Here's the description. {body}.",
        "What we see is {body}.",
    ],
}

_DOCTOR_QUESTIONS = [
    "Can you tell me more about that?",
    "I see. Anything else?",
    "Got it. And what about other symptoms?",
    "Understood. Let's continue.",
    "Thank you for sharing that. Go on.",
    "Okay, that's helpful. What else should I know?",
    "I appreciate you telling me. Let me ask about something else.",
    "Noted. Is there anything else you want to mention?",
]

_PATIENT_ACKS = [
    "Okay, doctor.",
    "I understand.",
    "That makes sense.",
    "Alright.",
    "Got it.",
    "Okay, thank you.",
    "I see.",
    "Sure, that's clear.",
]

_GREETINGS = [
    ("Doctor: Good morning! How are you feeling today?\nPatient: Not too great, honestly. That's why I'm here.",
     "Doctor: I'm sorry to hear that. Let's take a look at what's going on."),
    ("Doctor: Hello, thanks for coming in. What brings you here today?\nPatient: Hi doctor. I've been having some issues I wanted to discuss.",
     "Doctor: Of course, I'm here to help. Let's go through everything."),
    ("Doctor: Welcome. I've reviewed your chart — let's talk about what's going on.\nPatient: Thank you, doctor. I appreciate you seeing me.",
     "Doctor: Absolutely. Let's start from the beginning."),
    ("Doctor: Hi there, please have a seat. How can I help you today?\nPatient: Thanks, doctor. I have a few concerns I'd like to go over.",
     "Doctor: Sure, take your time. I'm listening."),
]

_CLOSINGS = [
    ("Doctor: Alright, I think we've covered everything. Do you have any questions for me?",
     "Patient: No, I think I understand everything. Thank you so much, doctor.",
     "Doctor: You're welcome. Don't hesitate to call if anything changes. Take care."),
    ("Doctor: That wraps up our discussion. Any concerns before we finish?",
     "Patient: I think I'm good. You've been very thorough, thank you.",
     "Doctor: Happy to help. Follow up with us if you need anything."),
    ("Doctor: Is there anything else you'd like to ask?",
     "Patient: No, I feel much better knowing what the plan is. Thanks, doctor.",
     "Doctor: Great. We'll get you on the right track. See you at the follow-up."),
    ("Doctor: Any last questions?",
     "Patient: Just one — should I be worried?",
     "Doctor: Based on what we've discussed, we have a solid plan. Let's take it step by step. You're in good hands.",
     "Patient: That's reassuring. Thank you, doctor."),
]


def transform_to_dialogue(transcription: str, specialty: str, description: str) -> str:
    """Convert structured medical notes into a natural doctor-patient dialogue."""
    if not transcription or not transcription.strip():
        return ""

    lines: list[str] = []

    # Opening greeting
    greeting = random.choice(_GREETINGS)
    for g in greeting:
        lines.append(g)
    lines.append(f"\nPatient: I'm here about {description.strip().lower().rstrip('.')}.")
    lines.append(f"Doctor: Right, this relates to {specialty.strip().lower()}. Let's go through it.\n")

    parts = SECTION_PATTERN.split(transcription)

    # Text before first section
    if parts[0].strip():
        lines.append(f"Doctor: {_clean(parts[0])}\n")

    i = 1
    turn_count = 0
    while i < len(parts) - 1:
        header = parts[i].strip().rstrip(":, ")
        body = _clean(parts[i + 1])
        i += 2

        if not body:
            continue

        header_upper = header.upper()

        if header_upper in PATIENT_SECTIONS:
            # Occasionally doctor asks a bridging question before patient speaks
            if turn_count > 0 and random.random() < 0.5:
                lines.append(f"Doctor: {random.choice(_DOCTOR_QUESTIONS)}\n")

            templates = _PATIENT_OPENERS.get(header_upper, ["Well, about {body}."])
            line = random.choice(templates).format(body=_lowercase_start(body))
            lines.append(f"Patient: {line}\n")

        elif header_upper in DOCTOR_SECTIONS:
            # Occasionally patient acknowledges before doctor continues
            if turn_count > 0 and random.random() < 0.4:
                lines.append(f"Patient: {random.choice(_PATIENT_ACKS)}\n")

            templates = _DOCTOR_OPENERS.get(header_upper, ["Regarding that, {body}."])
            line = random.choice(templates).format(body=_lowercase_start(body))
            lines.append(f"Doctor: {line}\n")

        else:
            lines.append(f"Doctor: About the {header.lower()} — {_lowercase_start(body)}.\n")

        turn_count += 1

    # Closing
    closing = random.choice(_CLOSINGS)
    lines.append("")
    for c in closing:
        lines.append(c)

    return "\n".join(lines)


def _lowercase_start(text: str) -> str:
    """Lowercase the first character unless it looks like a proper noun/abbreviation."""
    if not text:
        return text
    if len(text) > 1 and text[1].isupper():
        return text  # likely acronym or proper noun
    if text[0].isdigit():
        return text
    return text[0].lower() + text[1:]


def _clean(text: str) -> str:
    text = text.strip().strip(",").strip()
    text = re.sub(r"\s+", " ", text)
    return text


def load_samples(path: str, count: int) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        reader = list(csv.DictReader(f))

    # filter rows that actually have transcription text
    valid = [r for r in reader if r.get("transcription", "").strip()]
    selected = random.sample(valid, min(count, len(valid)))

    samples = []
    for row in selected:
        dialogue = transform_to_dialogue(
            row.get("transcription", ""),
            row.get("medical_specialty", "General"),
            row.get("description", "your condition"),
        )
        if dialogue:
            samples.append({
                "dialogue": dialogue,
                "specialty": row.get("medical_specialty", ""),
                "sample_name": row.get("sample_name", ""),
            })
    return samples


def save_transcripts(samples: list[dict], out_dir: str) -> list[str]:
    """Save transformed dialogues as .txt files. Returns list of saved paths."""
    os.makedirs(out_dir, exist_ok=True)
    saved = []
    for i, s in enumerate(samples, 1):
        # sanitize filename from sample_name
        name = re.sub(r"[^\w\s\-]", "", s["sample_name"]).strip().replace(" ", "_")
        if not name:
            name = f"sample_{i}"
        filename = f"{i:03d}_{name}.txt"
        filepath = os.path.join(out_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(f"Sample: {s['sample_name']}\n")
            f.write(f"Specialty: {s['specialty']}\n")
            f.write(f"{'='*60}\n\n")
            f.write(s["dialogue"])
        saved.append(filepath)
    return saved


# ── API calls ──────────────────────────────────────────────────────────────

async def post_summary(client: httpx.AsyncClient, text: str) -> tuple[float, dict | None]:
    start = time.perf_counter()
    resp = await client.post("/summaries/text", json={"text": text[:50000]})
    elapsed = time.perf_counter() - start
    if resp.status_code == 200:
        return elapsed, resp.json()
    print(f"  [POST FAIL {resp.status_code}] {resp.text[:150]}")
    return elapsed, None


async def patch_summary(client: httpx.AsyncClient, sid: int) -> float:
    patch = {"summary": "Manually reviewed and approved.", "action_items": ["Follow up in 2 weeks"]}
    start = time.perf_counter()
    resp = await client.patch(f"/summaries/{sid}", json=patch)
    elapsed = time.perf_counter() - start
    if resp.status_code != 200:
        print(f"  [PATCH FAIL {resp.status_code}] {resp.text[:150]}")
    return elapsed


# ── Runners ────────────────────────────────────────────────────────────────

async def run_sequential(client: httpx.AsyncClient, samples: list[dict], do_patch: bool):
    results = []
    for i, s in enumerate(samples, 1):
        print(f"\n── Sample {i}/{len(samples)}: {s['sample_name']} ({s['specialty']})")
        print(f"   Transcript length: {len(s['dialogue'])} chars")

        elapsed, data = await post_summary(client, s["dialogue"])
        results.append({"elapsed": elapsed, "data": data, "meta": s})

        if data:
            print(f"   POST    : {elapsed:.2f}s")
            print(f"   Summary : {data['summary'][:120]}...")
            print(f"   Points  : {len(data['key_points'])} | Actions: {len(data['action_items'])}")
            if do_patch:
                p_elapsed = await patch_summary(client, data["id"])
                print(f"   PATCH   : {p_elapsed:.4f}s")
        else:
            print(f"   FAILED  : {elapsed:.2f}s")

    return results


async def run_concurrent(client: httpx.AsyncClient, samples: list[dict], do_patch: bool):
    print(f"\nFiring {len(samples)} concurrent POST requests...")
    wall_start = time.perf_counter()
    tasks = [post_summary(client, s["dialogue"]) for s in samples]
    post_results = await asyncio.gather(*tasks)
    wall_elapsed = time.perf_counter() - wall_start

    successes = [(s, elapsed, data) for s, (elapsed, data) in zip(samples, post_results) if data]
    failures = len(samples) - len(successes)

    times = [elapsed for _, elapsed, _ in successes]

    print(f"\n── POST Results ──")
    print(f"   Total     : {len(samples)} ({len(successes)} ok, {failures} failed)")
    if times:
        print(f"   Wall time : {wall_elapsed:.2f}s (sequential estimate: {sum(t for t, _ in post_results):.1f}s)")
        print(f"   Avg       : {sum(times)/len(times):.2f}s")
        print(f"   Fastest   : {min(times):.2f}s")
        print(f"   Slowest   : {max(times):.2f}s")

    for s, elapsed, data in successes:
        print(f"\n   [{s['sample_name']}] ({elapsed:.2f}s)")
        print(f"   Summary: {data['summary'][:150]}...")
        print(f"   Key points: {len(data['key_points'])} | Actions: {len(data['action_items'])}")

    if do_patch and successes:
        print(f"\n── PATCH: updating {len(successes)} summaries concurrently...")
        patch_tasks = [patch_summary(client, data["id"]) for _, _, data in successes]
        wall_start = time.perf_counter()
        patch_times = await asyncio.gather(*patch_tasks)
        wall_elapsed = time.perf_counter() - wall_start
        print(f"   Wall time : {wall_elapsed:.4f}s")
        print(f"   Avg       : {sum(patch_times)/len(patch_times):.4f}s")

    return successes


async def main(args):
    print(f"Loading {args.count} random samples from CSV...")
    samples = load_samples(CSV_PATH, args.count)
    print(f"Loaded {len(samples)} samples\n")

    if not samples:
        print("No valid samples found!")
        return

    # Save transcripts to text files
    if args.save:
        saved = save_transcripts(samples, args.save)
        print(f"Saved {len(saved)} transcripts to {args.save}/")
        for p in saved:
            print(f"  {p}")
        print()

    # Show a preview of the first transformed transcript
    print(f"── Preview: {samples[0]['sample_name']} ──")
    preview = samples[0]["dialogue"][:600]
    print(preview)
    print("..." if len(samples[0]["dialogue"]) > 600 else "")

    if args.no_api:
        print("\n--no-api flag set, skipping API calls.")
    else:
        timeout = httpx.Timeout(120.0)
        async with httpx.AsyncClient(base_url=args.base_url, timeout=timeout) as client:
            if args.concurrent:
                await run_concurrent(client, samples, args.patch)
            else:
                await run_sequential(client, samples, args.patch)

    print(f"\n{'='*60}")
    print("  Simulation complete")
    print(f"{'='*60}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Simulate transcript workload from mtsamples.csv")
    parser.add_argument("--count", type=int, default=3, help="Number of samples to process (default: 3)")
    parser.add_argument("--concurrent", action="store_true", help="Run requests concurrently")
    parser.add_argument("--patch", action="store_true", help="Also test PATCH after each create")
    parser.add_argument("--save", type=str, metavar="DIR", help="Save transcripts to .txt files in DIR (e.g. --save transcripts)")
    parser.add_argument("--no-api", action="store_true", help="Only transform and save, skip API calls")
    parser.add_argument("--base-url", type=str, default=BASE_URL, help=f"API base URL (default: {BASE_URL})")
    args = parser.parse_args()
    asyncio.run(main(args))
