"""Download a fresh AI4S-Bench service root-volume snapshot to a local sparse image.

Reads snapshot blocks through the EBS Direct APIs, verifies each returned block,
then atomically replaces the previous local image only after a complete download.
"""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import ctypes
import hashlib
import json
import os
import shutil
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config


DEFAULT_INSTANCE_ID = "i-0ad6edfa737560f25"
DEFAULT_REGION = "us-east-1"
DEFAULT_OUTPUT_DIR = Path(r"D:\ai4sbench-service-backups")
BLOCK_SIZE = 512 * 1024
FSCTL_SET_SPARSE = 0x900C4


def make_sparse_file(file_obj: Any) -> None:
    if os.name != "nt":
        return
    import msvcrt
    from ctypes import wintypes

    device_io_control = ctypes.windll.kernel32.DeviceIoControl
    device_io_control.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    ]
    device_io_control.restype = wintypes.BOOL
    bytes_returned = wintypes.DWORD()
    handle = msvcrt.get_osfhandle(file_obj.fileno())
    ok = device_io_control(
        handle,
        FSCTL_SET_SPARSE,
        None,
        0,
        None,
        0,
        ctypes.byref(bytes_returned),
        None,
    )
    if not ok:
        raise ctypes.WinError()


def iter_snapshot_blocks(ebs: Any, snapshot_id: str):
    next_token = None
    while True:
        params: dict[str, Any] = {"SnapshotId": snapshot_id, "MaxResults": 10000}
        if next_token:
            params["NextToken"] = next_token
        page = ebs.list_snapshot_blocks(**params)
        yield page.get("Blocks", [])
        next_token = page.get("NextToken")
        if not next_token:
            break


def fetch_block(ebs: Any, snapshot_id: str, block: dict[str, Any]):
    response = ebs.get_snapshot_block(
        SnapshotId=snapshot_id,
        BlockIndex=block["BlockIndex"],
        BlockToken=block["BlockToken"],
    )
    data = response["BlockData"].read()
    checksum = base64.b64encode(hashlib.sha256(data).digest()).decode("ascii")
    if response.get("ChecksumAlgorithm") != "SHA256" or checksum != response.get("Checksum"):
        raise RuntimeError(f"SHA-256 mismatch for snapshot block {block['BlockIndex']}")
    return block["BlockIndex"], data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance-id", default=DEFAULT_INSTANCE_ID)
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--workers", type=int, default=48)
    parser.add_argument("--snapshot-id", help="resume a completed snapshot instead of creating one")
    parser.add_argument("--delete-snapshot-after-success", action="store_true")
    args = parser.parse_args()

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    if not output_dir.drive:
        raise RuntimeError(f"Output must be on a Windows drive: {output_dir}")
    target = output_dir / "service-root.raw"
    manifest_target = output_dir / "manifest.json"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    partial = output_dir / f"service-root.{stamp}.partial"
    manifest_partial = output_dir / f"manifest.{stamp}.partial.json"

    session = boto3.Session(region_name=args.region)
    config = Config(
        retries={"mode": "adaptive", "max_attempts": 10},
        max_pool_connections=max(args.workers, 10),
    )
    ec2 = session.client("ec2", config=config)
    ebs = session.client("ebs", config=config)

    snapshot_id = args.snapshot_id
    snapshot_created = False
    committed = False
    try:
        response = ec2.describe_instances(InstanceIds=[args.instance_id])
        instance = response["Reservations"][0]["Instances"][0]
        root_device = instance["RootDeviceName"]
        mapping = next(
            item for item in instance["BlockDeviceMappings"]
            if item["DeviceName"] == root_device and "Ebs" in item
        )
        volume_id = mapping["Ebs"]["VolumeId"]
        volume = ec2.describe_volumes(VolumeIds=[volume_id])["Volumes"][0]
        logical_size = int(volume["Size"]) * (1024**3)
        free_bytes = shutil.disk_usage(output_dir).free
        if free_bytes < logical_size:
            raise RuntimeError(
                f"D: has {free_bytes} free bytes; a {logical_size}-byte raw image needs that much space"
            )
        print(f"Instance {args.instance_id}; root volume {volume_id}; {volume['Size']} GiB", flush=True)

        if not snapshot_id:
            snapshot = ec2.create_snapshot(
                VolumeId=volume_id,
                Description=f"Local D-drive backup for {args.instance_id} at {stamp}",
                TagSpecifications=[
                    {
                        "ResourceType": "snapshot",
                        "Tags": [
                            {"Key": "Name", "Value": f"ai4sbench-service-local-backup-{stamp}"},
                            {"Key": "ai4sbench:backup-purpose", "Value": "local-d-drive-export"},
                        ],
                    }
                ],
            )
            snapshot_id = snapshot["SnapshotId"]
            snapshot_created = True
            print(f"Created {snapshot_id}; waiting for completion", flush=True)
            ec2.get_waiter("snapshot_completed").wait(
                SnapshotIds=[snapshot_id],
                WaiterConfig={"Delay": 15, "MaxAttempts": 240},
            )
        else:
            print(f"Resuming completed snapshot {snapshot_id}", flush=True)
        snap_info = ec2.describe_snapshots(SnapshotIds=[snapshot_id])["Snapshots"][0]
        if snap_info.get("State") != "completed":
            raise RuntimeError(f"Snapshot did not complete: {snap_info.get('State')}")

        estimated_bytes = int(snap_info.get("FullSnapshotSizeInBytes") or 0)
        if estimated_bytes and free_bytes < logical_size:
            raise RuntimeError(
                f"D: no longer has enough free space for the {logical_size}-byte raw image"
            )

        verified_blocks = 0
        written_bytes = 0
        write_lock = threading.Lock()
        with partial.open("w+b", buffering=0) as output:
            make_sparse_file(output)
            output.truncate(logical_size)
            with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
                for blocks in iter_snapshot_blocks(ebs, snapshot_id):
                    # Bound in-flight responses so a large EBS volume does not
                    # retain an entire page of 512 KiB blocks in memory.
                    batch_size = max(args.workers * 4, 1)
                    for start in range(0, len(blocks), batch_size):
                        batch = blocks[start : start + batch_size]
                        futures = [pool.submit(fetch_block, ebs, snapshot_id, block) for block in batch]
                        for future in concurrent.futures.as_completed(futures):
                            index, data = future.result()
                            if len(data) != BLOCK_SIZE:
                                raise RuntimeError(f"Unexpected block length at index {index}: {len(data)}")
                            with write_lock:
                                output.seek(index * BLOCK_SIZE)
                                output.write(data)
                            verified_blocks += 1
                            written_bytes += len(data)
                        if verified_blocks % 500 == 0:
                            print(
                                f"Progress: {verified_blocks} blocks verified ({written_bytes} bytes)",
                                flush=True,
                            )
                    output.flush()
                    print(f"Verified {verified_blocks} blocks ({written_bytes} bytes)", flush=True)
            output.flush()
            os.fsync(output.fileno())
            if partial.stat().st_size != logical_size:
                raise RuntimeError("Sparse image logical size does not match the source volume")

        manifest = {
            "instance_id": args.instance_id,
            "region": args.region,
            "root_device": root_device,
            "volume_id": volume_id,
            "snapshot_id": snapshot_id,
            "snapshot_created_at": snap_info["StartTime"].astimezone(timezone.utc).isoformat(),
            "snapshot_encrypted": snap_info.get("Encrypted"),
            "volume_size_gib": int(volume["Size"]),
            "logical_image_bytes": logical_size,
            "verified_blocks": verified_blocks,
            "downloaded_block_bytes": written_bytes,
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "verification": "every returned EBS block passed its AWS SHA-256 checksum",
            "image_format": "raw disk image; blocks omitted by EBS are represented as zero",
        }
        manifest_partial.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        os.replace(partial, target)
        os.replace(manifest_partial, manifest_target)
        committed = True
        print(f"Backup verified and replaced: {target}", flush=True)

        # The complete local image is now the retained backup; remove only this
        # temporary snapshot. The AMI and its original snapshot are untouched.
        if snapshot_created or args.delete_snapshot_after_success:
            ec2.delete_snapshot(SnapshotId=snapshot_id)
            print(f"Deleted temporary AWS snapshot {snapshot_id}", flush=True)
        return 0
    finally:
        if not committed:
            for path in (partial, manifest_partial):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            if snapshot_id and snapshot_created:
                try:
                    ec2.delete_snapshot(SnapshotId=snapshot_id)
                    print(f"Deleted incomplete temporary snapshot {snapshot_id}")
                except Exception as exc:  # report cleanup failures without masking the original error
                    print(f"Could not delete temporary snapshot {snapshot_id}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
