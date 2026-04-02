"""
Benchmark script for Transcript Summarizer API.

Simulates concurrent users hitting POST /text, PATCH /{id}, and GET /{id}.

Usage:
    python benchmark.py                  # defaults: 5 concurrent, http://localhost:8000
    python benchmark.py --users 20 --base-url http://localhost:8000
"""

import asyncio
import time
import argparse
import httpx

BASE_URL = "http://localhost:8000"

SAMPLE_TRANSCRIPTS = [
    "Pharmacist: Good morning, how can I help you today? Patient: Hi, I was calling about my prescription for lisinopril. I've been experiencing some dizziness. Pharmacist: I see. How long have you been taking it? Patient: About two weeks now. Pharmacist: Dizziness can be a common side effect when starting lisinopril. Are you taking it in the morning or evening? Patient: Morning. Pharmacist: Try taking it at bedtime instead, that often helps with dizziness. If it persists beyond another week, contact your doctor. Patient: Okay, I'll try that. Also, can I take ibuprofen for a headache? Pharmacist: Actually, NSAIDs like ibuprofen can interact with lisinopril. I'd recommend acetaminophen instead. Patient: Got it, thanks! Pharmacist: You're welcome. Any other questions? Patient: No, that's all. Pharmacist: Great, take care!",
    "Pharmacist: Hello, this is Sarah from City Pharmacy. Patient: Hi Sarah, I'm calling about my metformin refill. Pharmacist: Let me pull up your file. Yes, I see your prescription. It looks like you're due for a refill. Patient: Yes, and my doctor also mentioned increasing my dosage from 500mg to 1000mg. Pharmacist: I don't see a new prescription for the dosage change yet. You'll need your doctor to send us an updated prescription. Patient: Oh, I thought he already did. Pharmacist: I'd recommend calling his office to confirm. In the meantime, I can refill your current 500mg prescription. Patient: Yes please, I'm almost out. Pharmacist: It'll be ready in about an hour. Also, remember to take it with food to minimize stomach upset. Patient: Will do. Thanks Sarah! Pharmacist: Take care!",
    "Pharmacist: Pharmacy, how may I help you? Patient: I need to know if I can take my allergy medication with the new antibiotic my doctor prescribed. Pharmacist: Sure, what medications are we talking about? Patient: Cetirizine for allergies, and amoxicillin was just prescribed for a sinus infection. Pharmacist: Good news, those two are safe to take together. No significant interactions. Patient: That's a relief. How should I take the amoxicillin? Pharmacist: Take it every 8 hours with or without food. Make sure to complete the full course even if you feel better. Patient: It's a 10-day course, right? Pharmacist: Yes, 10 days. And keep taking your cetirizine as usual. If you notice any rash or unusual symptoms, stop both and call us immediately. Patient: Understood. Thank you! Pharmacist: You're welcome, feel better soon!",
]


def print_header(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def print_results(label: str, times: list[float], errors: int):
    if not times:
        print(f"  {label}: all {errors} requests failed")
        return
    avg = sum(times) / len(times)
    fastest = min(times)
    slowest = max(times)
    print(f"  {label}")
    print(f"    Requests : {len(times)} ok, {errors} failed")
    print(f"    Avg      : {avg:.3f}s")
    print(f"    Fastest  : {fastest:.3f}s")
    print(f"    Slowest  : {slowest:.3f}s")


async def bench_create(client: httpx.AsyncClient, text: str) -> tuple[float, int | None]:
    """POST /summaries/text — returns (elapsed, created_id or None)."""
    start = time.perf_counter()
    resp = await client.post("/summaries/text", json={"text": text})
    elapsed = time.perf_counter() - start
    if resp.status_code == 200:
        return elapsed, resp.json()["id"]
    print(f"    [CREATE] {resp.status_code}: {resp.text[:120]}")
    return elapsed, None


async def bench_patch(client: httpx.AsyncClient, summary_id: int, patch: dict) -> float:
    """PATCH /summaries/{id} — returns elapsed."""
    start = time.perf_counter()
    resp = await client.patch(f"/summaries/{summary_id}", json=patch)
    elapsed = time.perf_counter() - start
    if resp.status_code != 200:
        print(f"    [PATCH] {resp.status_code}: {resp.text[:120]}")
    return elapsed


async def bench_get(client: httpx.AsyncClient, summary_id: int) -> float:
    """GET /summaries/{id} — returns elapsed."""
    start = time.perf_counter()
    resp = await client.get(f"/summaries/{summary_id}")
    elapsed = time.perf_counter() - start
    if resp.status_code != 200:
        print(f"    [GET] {resp.status_code}: {resp.text[:120]}")
    return elapsed


async def run_benchmark(base_url: str, num_users: int):
    timeout = httpx.Timeout(120.0)
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as client:

        # --------------------------------------------------
        # Phase 1: Concurrent CREATES
        # --------------------------------------------------
        print_header(f"Phase 1: {num_users} concurrent POST /summaries/text")

        tasks = [
            bench_create(client, SAMPLE_TRANSCRIPTS[i % len(SAMPLE_TRANSCRIPTS)])
            for i in range(num_users)
        ]

        wall_start = time.perf_counter()
        results = await asyncio.gather(*tasks)
        wall_elapsed = time.perf_counter() - wall_start

        create_times = [r[0] for r in results]
        created_ids = [r[1] for r in results if r[1] is not None]
        create_errors = num_users - len(created_ids)

        print_results("POST /summaries/text", create_times, create_errors)
        print(f"    Wall time: {wall_elapsed:.3f}s  (sequential would be ~{sum(create_times):.1f}s)")
        print(f"    Created IDs: {created_ids}")

        if not created_ids:
            print("\n  No summaries created — skipping PATCH and GET phases.")
            return

        # --------------------------------------------------
        # Phase 2: Concurrent PATCHES
        # --------------------------------------------------
        patches = [
            {"summary": "Updated summary via benchmark."},
            {"key_points": ["benchmarked point 1", "benchmarked point 2"]},
            {"action_items": ["follow up on benchmark results"]},
            {"summary": "Another edit.", "key_points": ["edited key point"]},
        ]

        print_header(f"Phase 2: {len(created_ids)} concurrent PATCH /summaries/{{id}}")

        patch_tasks = [
            bench_patch(client, sid, patches[i % len(patches)])
            for i, sid in enumerate(created_ids)
        ]

        wall_start = time.perf_counter()
        patch_times = await asyncio.gather(*patch_tasks)
        wall_elapsed = time.perf_counter() - wall_start

        patch_errors = 0  # errors already printed inside bench_patch
        print_results("PATCH /summaries/{id}", list(patch_times), patch_errors)
        print(f"    Wall time: {wall_elapsed:.3f}s")

        # --------------------------------------------------
        # Phase 3: Concurrent GETS (verify patches applied)
        # --------------------------------------------------
        print_header(f"Phase 3: {len(created_ids)} concurrent GET /summaries/{{id}}")

        get_tasks = [bench_get(client, sid) for sid in created_ids]

        wall_start = time.perf_counter()
        get_times = await asyncio.gather(*get_tasks)
        wall_elapsed = time.perf_counter() - wall_start

        print_results("GET /summaries/{id}", list(get_times), 0)
        print(f"    Wall time: {wall_elapsed:.3f}s")

        # --------------------------------------------------
        # Phase 4: Mixed workload
        # --------------------------------------------------
        print_header(f"Phase 4: Mixed workload ({num_users} ops)")

        mixed_tasks = []
        for i in range(num_users):
            match i % 3:
                case 0:
                    mixed_tasks.append(("CREATE", bench_create(client, SAMPLE_TRANSCRIPTS[i % len(SAMPLE_TRANSCRIPTS)])))
                case 1:
                    sid = created_ids[i % len(created_ids)]
                    mixed_tasks.append(("PATCH", bench_patch(client, sid, patches[i % len(patches)])))
                case 2:
                    sid = created_ids[i % len(created_ids)]
                    mixed_tasks.append(("GET", bench_get(client, sid)))

        wall_start = time.perf_counter()
        mixed_results = await asyncio.gather(*[t[1] for t in mixed_tasks])
        wall_elapsed = time.perf_counter() - wall_start

        grouped: dict[str, list[float]] = {"CREATE": [], "PATCH": [], "GET": []}
        for (label, _), result in zip(mixed_tasks, mixed_results):
            elapsed = result[0] if isinstance(result, tuple) else result
            grouped[label].append(elapsed)

        for label, times in grouped.items():
            print_results(label, times, 0)
        print(f"    Total wall time: {wall_elapsed:.3f}s")

    print(f"\n{'='*60}")
    print("  Benchmark complete")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Benchmark the Transcript Summarizer API")
    parser.add_argument("--users", type=int, default=5, help="Number of concurrent simulated users (default: 5)")
    parser.add_argument("--base-url", type=str, default=BASE_URL, help=f"API base URL (default: {BASE_URL})")
    args = parser.parse_args()

    print(f"\nTarget: {args.base_url}  |  Concurrent users: {args.users}")
    asyncio.run(run_benchmark(args.base_url, args.users))
