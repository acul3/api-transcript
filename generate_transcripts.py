"""
Generate realistic 5-minute doctor-patient phone call transcripts
from mtsamples.csv using OpenAI, then save as .txt files.

Usage:
    python generate_transcripts.py                     # 10 transcripts
    python generate_transcripts.py --count 20          # 20 transcripts
    python generate_transcripts.py --count 5 --test    # generate + send to summary API
"""

import asyncio
import csv
import os
import random
import re
import time
import argparse
from dotenv import load_dotenv
from openai import AsyncOpenAI
import httpx

load_dotenv()

CSV_PATH = os.getenv("CSV_PATH", "mtsamples.csv")
OUT_DIR = "transcripts"
BASE_URL = "http://localhost:8000"

client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

SYSTEM_PROMPT = """\
You are a screenplay writer specializing in medical phone call transcripts.

Given medical case notes, generate a REALISTIC 5-minute phone call transcript \
between a doctor/pharmacist and a patient. The call should feel like a real \
recorded and transcribed phone conversation.

CRITICAL — the transcript MUST contain these summarizable elements woven naturally into dialogue:

1. KEY POINTS (at least 5-7 distinct ones):
   - Diagnosis or condition name stated clearly
   - Symptoms discussed with specifics (duration, severity, frequency)
   - Test results or findings mentioned with actual values/numbers
   - Medication names, dosages, and frequency ("Take 500mg of metformin twice daily with meals")
   - Medical history relevant to the current issue
   - Risk factors or complications mentioned
   - Lifestyle factors discussed (diet, exercise, smoking, alcohol)

2. ACTION ITEMS (at least 4-6 clear ones):
   - Specific medication changes ("Switch from ibuprofen to acetaminophen")
   - Lab work or tests to schedule ("We need to get blood work done before your next visit")
   - Follow-up appointment with date/timeframe ("Let's schedule a follow-up in two weeks")
   - Lifestyle changes recommended ("Try to cut your sodium intake to under 2000mg a day")
   - Referrals to specialists ("I'm going to refer you to a cardiologist")
   - Things the patient should monitor ("Keep track of your blood pressure readings every morning")
   - Warning signs to watch for ("If you notice any chest pain or shortness of breath, go to the ER immediately")
   - Prescriptions to pick up ("I'll send the prescription to your pharmacy, you can pick it up tomorrow")

3. SPECIFIC DETAILS that make summaries rich:
   - Actual numbers: blood pressure readings, weight, lab values, dosages
   - Dates and timeframes: "since last March", "for the past 3 weeks", "come back in 10 days"
   - Medication interactions or side effects discussed
   - Insurance or cost concerns if relevant
   - Patient's questions and doctor's clear answers

Conversation style rules:
- Output ONLY the transcript, no titles or metadata
- Format each line as "Speaker: dialogue" (use "Doctor:" and "Patient:")
- Make it 900-1300 words long (simulating ~5 minutes of real talk at ~150 wpm)
- Include natural conversational elements:
  * Greetings and small talk at the start
  * "Um", "uh", "well", "you know", "let me see" — natural filler words
  * Interruptions and overlapping thoughts ("Oh sorry, go ahead—")
  * Patient asking clarifying questions ("Wait, what does that mean exactly?")
  * Doctor rephrasing medical jargon into simple language
  * Pauses noted as "[pause]" occasionally
  * Background acknowledgments ("Mhmm", "Right", "I see", "Okay")
  * Patient expressing emotions (worry, relief, frustration, confusion)
  * Spelling out medication names ("That's A-M-O-X-I-C-I-L-L-I-N")
  * Phone-specific elements ("Can you hear me okay?", "Sorry, you cut out for a second")
- Doctor should SUMMARIZE the plan at the end: "So just to recap what we discussed..."
- Include a proper goodbye with confirmed next steps
- Make the patient personality varied — some anxious, some calm, some chatty, some brief
- The doctor should be warm but professional
- Do NOT use markdown formatting, headers, or bullet points
- Make it feel like a REAL transcribed phone call, not a scripted dialogue
"""


def load_csv_samples(path: str, count: int) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    valid = [r for r in rows if r.get("transcription", "").strip()
             and len(r.get("transcription", "")) > 300]
    return random.sample(valid, min(count, len(valid)))


async def generate_transcript(row: dict, index: int) -> dict:
    """Use OpenAI to transform medical notes into a realistic phone transcript."""
    specialty = row.get("medical_specialty", "General").strip()
    sample_name = row.get("sample_name", "Unknown").strip()
    description = row.get("description", "").strip()
    transcription = row.get("transcription", "").strip()

    user_prompt = f"""Medical Specialty: {specialty}
Case: {sample_name}
Description: {description}

--- Original Medical Notes ---
{transcription[:6000]}
--- End Notes ---

Generate a realistic 5-minute phone call transcript between a doctor/pharmacist \
and a patient based on the above medical case. Make it conversational, natural, \
and include all the key medical details woven into the dialogue."""

    try:
        response = await client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.85,
            max_tokens=2500,
        )
        transcript = response.choices[0].message.content.strip()
        word_count = len(transcript.split())
        print(f"  [{index:02d}] {sample_name} — {word_count} words")
        return {
            "transcript": transcript,
            "specialty": specialty,
            "sample_name": sample_name,
            "description": description,
            "word_count": word_count,
        }
    except Exception as e:
        print(f"  [{index:02d}] FAILED: {sample_name} — {e}")
        return None


def save_transcript(result: dict, index: int, out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    name = re.sub(r"[^\w\s\-]", "", result["sample_name"]).strip().replace(" ", "_")
    if not name:
        name = f"sample_{index}"
    filename = f"{index:03d}_{name}.txt"
    filepath = os.path.join(out_dir, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(f"Case: {result['sample_name']}\n")
        f.write(f"Specialty: {result['specialty']}\n")
        f.write(f"Description: {result['description']}\n")
        f.write(f"Word Count: {result['word_count']}\n")
        f.write(f"{'='*60}\n\n")
        f.write(result["transcript"])

    return filepath


async def test_with_api(results: list[dict], base_url: str):
    """Send generated transcripts to the summary API for end-to-end test."""
    print(f"\n{'='*60}")
    print(f"  Sending {len(results)} transcripts to summary API")
    print(f"{'='*60}\n")

    timeout = httpx.Timeout(120.0)
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as http:
        tasks = []
        for r in results:
            tasks.append(http.post("/summaries/text", json={"text": r["transcript"][:50000]}))

        wall_start = time.perf_counter()
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        wall_elapsed = time.perf_counter() - wall_start

        ok = 0
        for r, resp in zip(results, responses):
            if isinstance(resp, Exception):
                print(f"  [FAIL] {r['sample_name']}: {resp}")
                continue
            if resp.status_code == 200:
                data = resp.json()
                ok += 1
                print(f"  [OK] {r['sample_name']}")
                print(f"       Summary: {data['summary'][:150]}...")
                print(f"       Key points: {len(data['key_points'])} | Actions: {len(data['action_items'])}")
            else:
                print(f"  [FAIL] {r['sample_name']}: {resp.status_code} {resp.text[:100]}")

        print(f"\n  Results: {ok}/{len(results)} succeeded in {wall_elapsed:.2f}s")


async def main(args):
    print(f"Loading {args.count} random medical cases from CSV...")
    samples = load_csv_samples(CSV_PATH, args.count)
    print(f"Loaded {len(samples)} cases\n")

    # Generate transcripts concurrently (batch of 5 at a time to avoid rate limits)
    batch_size = 5
    all_results = []

    for batch_start in range(0, len(samples), batch_size):
        batch = samples[batch_start:batch_start + batch_size]
        batch_num = batch_start // batch_size + 1
        total_batches = (len(samples) + batch_size - 1) // batch_size
        print(f"── Generating batch {batch_num}/{total_batches} ({len(batch)} transcripts)...")

        tasks = [
            generate_transcript(row, batch_start + i + 1)
            for i, row in enumerate(batch)
        ]
        results = await asyncio.gather(*tasks)
        all_results.extend([r for r in results if r])

    print(f"\nGenerated {len(all_results)}/{len(samples)} transcripts")

    # Save to files
    saved = []
    for i, r in enumerate(all_results, 1):
        path = save_transcript(r, i, args.output)
        saved.append(path)

    print(f"\nSaved {len(saved)} files to {args.output}/:")
    for p in saved:
        print(f"  {p}")

    # Stats
    word_counts = [r["word_count"] for r in all_results]
    if word_counts:
        print(f"\n── Word count stats ──")
        print(f"   Avg:     {sum(word_counts)//len(word_counts)} words")
        print(f"   Min:     {min(word_counts)} words")
        print(f"   Max:     {max(word_counts)} words")
        print(f"   ~Time:   {sum(word_counts)//150} min total ({sum(word_counts)//150//len(word_counts)} min avg per call)")

    # Optionally test with summary API
    if args.test:
        await test_with_api(all_results, args.base_url)

    print(f"\n{'='*60}")
    print("  Done")
    print(f"{'='*60}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate realistic 5-min medical phone transcripts")
    parser.add_argument("--count", type=int, default=10, help="Number of transcripts to generate (default: 10)")
    parser.add_argument("--output", type=str, default=OUT_DIR, help=f"Output directory (default: {OUT_DIR})")
    parser.add_argument("--test", action="store_true", help="Also send to summary API for end-to-end test")
    parser.add_argument("--base-url", type=str, default=BASE_URL, help=f"API base URL (default: {BASE_URL})")
    args = parser.parse_args()
    asyncio.run(main(args))
