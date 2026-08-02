#!/usr/bin/env python3
import argparse
import base64
import hashlib
import urllib.request
import uuid
from urllib.error import HTTPError
from xml.etree import ElementTree

import boto3
from botocore.exceptions import ClientError
from botocore.config import Config


def stack_bucket(session, stack_name, region):
    cloudformation = session.client("cloudformation", region_name=region)
    stack = cloudformation.describe_stacks(StackName=stack_name)["Stacks"][0]
    outputs = {item["OutputKey"]: item["OutputValue"] for item in stack.get("Outputs", ())}
    return outputs["MediaBucketName"]


def run(stack_name, region):
    session = boto3.Session(profile_name="default", region_name=region)
    bucket = stack_bucket(session, stack_name, region)
    s3 = session.client("s3", region_name=region, config=Config(signature_version="s3v4"))
    key = f"uploads/verification/browser-ingestion-{uuid.uuid4()}/part.bin"
    body = b"mediacms-browser-ingestion-smoke" * 200_000
    checksum = base64.b64encode(hashlib.sha256(body).digest()).decode("ascii")
    upload_id = None
    try:
        upload_id = s3.create_multipart_upload(
            Bucket=bucket,
            Key=key,
            ContentType="application/octet-stream",
            ChecksumAlgorithm="SHA256",
            ChecksumType="COMPOSITE",
        )["UploadId"]
        url = s3.generate_presigned_url(
            "upload_part",
            Params={
                "Bucket": bucket,
                "Key": key,
                "UploadId": upload_id,
                "PartNumber": 1,
                "ChecksumSHA256": checksum,
            },
            ExpiresIn=900,
            HttpMethod="PUT",
        )
        request = urllib.request.Request(
            url,
            data=body,
            method="PUT",
            headers={"x-amz-checksum-sha256": checksum},
        )
        try:
            response = urllib.request.urlopen(request, timeout=60)
        except HTTPError as error:
            document = ElementTree.fromstring(error.read())
            code = document.findtext("Code") or "Unknown"
            message = document.findtext("Message") or "S3 rejected the request."
            raise RuntimeError(f"Presigned upload failed: {code}: {message}") from error
        with response:
            if response.status != 200:
                raise RuntimeError(f"Presigned upload returned HTTP {response.status}.")
        parts = s3.list_parts(Bucket=bucket, Key=key, UploadId=upload_id)["Parts"]
        if len(parts) != 1 or parts[0]["Size"] != len(body):
            raise RuntimeError("ListParts evidence does not match the uploaded Part.")
        s3.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)
        upload_id = None
        remaining = s3.list_multipart_uploads(Bucket=bucket, Prefix=key).get("Uploads", ())
        if remaining:
            raise RuntimeError("Verification Multipart upload remains after abort.")
        try:
            s3.head_object(Bucket=bucket, Key=key)
        except ClientError as error:
            if error.response["Error"]["Code"] not in {"404", "NoSuchKey", "NotFound"}:
                raise
        else:
            raise RuntimeError("Verification object unexpectedly exists.")
    finally:
        if upload_id is not None:
            s3.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)


def main():
    parser = argparse.ArgumentParser(description="Verify the browser Multipart upload path and exact cleanup.")
    parser.add_argument("--stack", default="mediacms-dev")
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()
    run(args.stack, args.region)
    print("PASS: presigned Multipart upload, ListParts reconciliation, exact abort, and no residue")


if __name__ == "__main__":
    main()
