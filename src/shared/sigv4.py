"""SigV4 request signing for internal Ledger calls — replaces the retired internal_api_key
shared secret. Matches what the Ledger's InternalCallerFilter expects: caller identity proven
by a signed request, verified at the edge (an API Gateway AWS_IAM authorizer, or an ALB/sidecar
doing the same), not a shared static secret. That edge verification is separate infra this
module doesn't provide on its own — see the module docstring note below.

__author__ = "Van Vo"
"""

from __future__ import annotations

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

# "execute-api" is the service name an API Gateway AWS_IAM authorizer signs/verifies against.
_SIGNING_SERVICE = "execute-api"


def sign_headers(method: str, url: str, headers: dict[str, str], body: bytes = b"") -> dict[str, str]:
    """Signs an outbound internal request with this Lambda's own execution-role credentials.

    `body` must be the exact bytes that will actually be sent — the signature covers a hash of
    the payload, so signing anything else invalidates it at verification. Callers that send a
    JSON body must serialize it once, sign those bytes, and send those same bytes (not re-encode
    via a library's own `json=` convenience param, which may not byte-for-byte match).

    Note: this only produces a signed request. Verifying it requires an edge in front of the
    Ledger that checks SigV4 signatures (e.g. API Gateway with an AWS_IAM authorizer) — the
    Ledger's InternalCallerFilter deliberately holds no secret and does no verification itself,
    trusting only the caller-ARN header that verified edge forwards. Provisioning that edge is a
    separate infra task, not something this function can provide alone.

    Raises:
        RuntimeError: no AWS credentials are available to sign with (e.g. no execution role).
    """
    session = boto3.Session()
    credentials = session.get_credentials()
    if credentials is None:
        raise RuntimeError("No AWS credentials available to sign the internal request.")

    region = session.region_name or "us-east-1"
    request = AWSRequest(method=method, url=url, data=body, headers=dict(headers))
    SigV4Auth(credentials, _SIGNING_SERVICE, region).add_auth(request)
    return dict(request.headers)
