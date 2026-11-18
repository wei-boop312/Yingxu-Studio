"""One explicit authenticated catalogue GET, never a model inference or permission change."""

import hashlib
import hmac

import httpx

from visual_platform.providers.bailian_connection import BailianConnection
from visual_platform.providers.qwen_image import strict_document
from visual_platform.providers.runtime import credential_fingerprint

MAX_METADATA_BODY = 2 * 1024 * 1024


def probe_metadata(configuration, key, *, kind="catalogue", model=None, transport=None):
    if kind not in ("catalogue", "permissions"):
        raise ValueError("unsupported_read_only_metadata_kind")
    configuration = BailianConnection.model_validate(configuration.model_dump(), strict=True)
    model = configuration.model if model is None else model
    if model not in (configuration.model, "qwen3.8-flash"):
        raise ValueError("unsupported_metadata_model")
    if not hmac.compare_digest(configuration.credential_fingerprint, credential_fingerprint(key)):
        raise ValueError("metadata_credential_mismatch")
    report = {
        "result": "METADATA_UNREACHABLE",
        "configuration_sha256": configuration.digest(),
        "model": model,
        "metadata_kind": kind,
        "metadata_get_requests": 1,
        "model_inference_requests": 0,
        "paid_model_calls": 0,
        "remote_authentication_observed": False,
        "model_listed": False,
        "generation_permission_verified": False,
        "workspace_inference_permission_observed": False,
        "provider_verified": False,
        "price_reviewed": False,
        "execution_enabled": False,
        "captured_body_complete": False,
    }
    captured = bytearray()
    parameters = {"model": model, "page_no": 1, "page_size": 1}
    parameters.update(
        {"language": "zh-CN"}
        if kind == "catalogue"
        else {"authorization_scope": "AUTHORIZED", "action": "INFERENCE"}
    )
    try:
        with httpx.Client(
            transport=transport, trust_env=False, follow_redirects=False, timeout=15
        ) as client:
            with client.stream(
                "GET",
                configuration.endpoint_origin
                + "/api/v1/models"
                + ("/permissions" if kind == "permissions" else ""),
                params=parameters,
                headers={
                    "Authorization": "Bearer " + key,
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                },
            ) as response:
                report["http_status"] = response.status_code
                chunks = (response.content,) if response.is_stream_consumed else response.iter_raw()
                for chunk in chunks:
                    remaining = MAX_METADATA_BODY + 1 - len(captured)
                    captured.extend(chunk[:remaining])
                    if len(captured) > MAX_METADATA_BODY:
                        break
                report["captured_body_complete"] = len(captured) <= MAX_METADATA_BODY
                if not report["captured_body_complete"]:
                    report["result"] = "METADATA_BODY_REJECTED"
                elif response.status_code in (401, 403):
                    report["result"] = "METADATA_ACCESS_DENIED"
                elif 300 <= response.status_code < 400:
                    report["result"] = "METADATA_REDIRECT_REJECTED"
                elif response.status_code != 200:
                    report["result"] = "METADATA_HTTP_ERROR"
                elif response.headers.get("content-encoding", "identity").lower() not in (
                    "",
                    "identity",
                ):
                    report["result"] = "METADATA_BODY_REJECTED"
                else:
                    document = strict_document(bytes(captured))
                    output = document.get("output")
                    if (
                        document.get("success") is not True
                        or document.get("code") is not None
                        or not isinstance(output, dict)
                        or type(output.get("models" if kind == "catalogue" else "permissions"))
                        is not list
                    ):
                        raise ValueError("unverified_catalogue_schema")
                    models = output["models" if kind == "catalogue" else "permissions"]
                    if len(models) > 1 or any(
                        type(model) is not dict or type(model.get("model")) is not str
                        for model in models
                    ):
                        raise ValueError("unexpected_catalogue_models")
                    report["remote_authentication_observed"] = True
                    report["model_listed"] = len(models) == 1 and models[0]["model"] == model
                    report["image_generation_capability_listed"] = bool(
                        report["model_listed"]
                        and type(models[0].get("capabilities")) is list
                        and "IG" in models[0]["capabilities"]
                    )
                    report["result"] = (
                        "REMOTE_MODEL_METADATA_READ"
                        if report["model_listed"]
                        else "MODEL_NOT_LISTED_IN_METADATA"
                    )
                    if kind == "permissions":
                        if report["model_listed"]:
                            permissions = models[0].get("permissions")
                            if (
                                type(permissions) is not dict
                                or type(permissions.get("inference")) is not bool
                            ):
                                report["remote_authentication_observed"] = False
                                raise ValueError("unverified_permission_schema")
                            report["workspace_inference_permission_observed"] = permissions[
                                "inference"
                            ]
                        report["result"] = (
                            "WORKSPACE_PERMISSION_METADATA_READ"
                            if report["workspace_inference_permission_observed"]
                            else "WORKSPACE_PERMISSION_NOT_CONFIRMED"
                        )
    except (httpx.HTTPError, ValueError) as error:
        report["remote_authentication_observed"] = False
        report["model_listed"] = False
        report["workspace_inference_permission_observed"] = False
        report["result"] = (
            "METADATA_UNREACHABLE"
            if isinstance(error, httpx.HTTPError)
            else "METADATA_BODY_REJECTED"
        )
        report["error_type"] = type(error).__name__
    raw = bytes(captured)
    report["captured_body_sha256"] = hashlib.sha256(raw).hexdigest()
    return report, raw
