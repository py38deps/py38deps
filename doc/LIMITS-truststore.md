# LIMITS: truststore on old Python versions

This document records behavioral differences between the truststore backport
(repo/truststore) and upstream (sethmlarson/truststore) **at runtime** on old
Python versions. The backport targets Python 3.8+ while upstream requires 3.10+
(the `>= 3.10` requirement exists since the initial commit in 2022-01), so every
difference below is specific to our fork. Test-suite and type-hint differences are
out of scope.

## 1. The peer certificate chain API is unavailable on Python 3.8/3.9 (core limitation)

`ssl.SSLObject.get_unverified_chain()` only became public in Python 3.13 (gh-109109)
but has been available on the internal `_sslobj` since CPython 3.10. On 3.8/3.9 the
`_ssl` C extension does not implement it at all — the inner `_sslobj` only exposes
`getpeercert()` (leaf certificate only). This is a hard interpreter-level limitation:

- There is no pure-Python way to retrieve the server-sent certificate chain on
  3.8/3.9; the chain lives inside the OpenSSL `SSL*` structure and Python has no
  access to it.
- A ctypes-based extraction of the `SSL*` pointer would require hacking CPython's
  object memory layout (unstable across versions) and calling
  `SSL_get_peer_cert_chain()` — which is not possible on Windows anyway because
  CPython statically links OpenSSL into `_ssl.pyd` (no exported symbols).

Therefore `_verify_peercerts()` in `src/truststore/_api.py` falls back to verifying
**only the leaf certificate** on Python < 3.10:

```python
if hasattr(sslobj, "get_unverified_chain"):
    cert_bytes = _get_unverified_chain_bytes(sslobj)
else:
    # Python < 3.10: leaf certificate only; the OS trust store builds the chain.
    leaf_cert = sock_or_sslobj.getpeercert(binary_form=True)
    cert_bytes = [leaf_cert] if leaf_cert is not None else []
```

The verification itself is still fail-closed: invalid certificates are rejected,
only the *chain building* is delegated to the OS instead of using the server-sent
chain.

## 2. Per-platform impact of the leaf-only fallback

| Platform | Impact |
| --- | --- |
| Linux | **None.** `_openssl._verify_peercerts_impl` is a no-op; verification is done entirely by OpenSSL inside the handshake, which has the full chain. |
| Windows | **Practically none.** `CertGetCertificateChain` builds the chain from the system root/intermediate stores and performs AIA network fetching when intermediates are missing (default). Measured on real badssl hosts, the rejection error codes are byte-identical between cp38 (leaf-only) and cp310 (full chain): `CERT_E_CN_NO_MATCH` (0x800B010F), `CERT_E_EXPIRED` (0x800B0101), `CERT_E_UNTRUSTEDROOT` (0x800B0109). |
| macOS | **Small residual difference.** `SecTrustCreateWithCertificates` builds the chain from the system keychain but does **not** fetch over the network by default. To compensate, the backport calls `SecTrustSetNetworkFetchAllowed(trust, True)` on Python < 3.10 (macOS 10.9+; wrapped in try/except for 10.8). The call lives inside `_verify_peercerts_impl()` rather than at module level so that macOS 10.8 does not fail the whole module import. |

Residual edge case on all platforms: a server whose chain contains an intermediate
certificate that is absent from the OS stores **and** unreachable via AIA/network
fetching would be rejected on 3.8/3.9 while 3.10+ would accept it. This is the only
known behavioral gap and it errs on the safe side.

## 3. macOS: CERT_NONE tolerates any SecTrust failure (backport-only change)

On macOS, truststore disables OpenSSL verification during the handshake and then
re-verifies the peer against the system trust store via `SecTrustEvaluateWithError`.
Upstream tolerates only two specific failure codes when the caller did not require
verification (`verify_mode != ssl.CERT_REQUIRED`): `errSecNotTrusted` and
`errSecCertificateExpired`; any other SecTrust failure is raised as
`ssl.SSLCertVerificationError`.

The backport additionally tolerates **any** SecTrust failure when
`verify_mode == ssl.CERT_NONE`. This is needed because on 3.8/3.9 only the leaf
certificate is handed to SecTrust (section 1), which then fetches the missing chain
over the network — a path that can surface different error codes than full-chain
verification (e.g. `errSecNotTrusted` vs `errSecCertificateExpired` after an
intermediate CA expired, observed with `expired.badssl.com` in CI). `CERT_NONE`
semantically disables certificate verification, so accepting the connection matches
the behavior of 3.10+ (where the full-chain path happens to hit a tolerated code)
and of non-macOS platforms. `CERT_OPTIONAL` keeps the upstream allow-list behavior.

## 4. Other runtime differences

- **`requires-python`**: `>= 3.8` (upstream: `>= 3.10`), so the backport installs on
  3.8/3.9 instead of failing at resolution time.
- **Import gate removed**: upstream `src/truststore/__init__.py` raises
  `ImportError` below 3.10; the backport removed that gate, so on 3.8/3.9 the module
  imports normally and uses the leaf-only verification path (section 1) instead.
  The import-time chain-API probe for non-CPython runtimes is kept (cpython < 3.10
  uses the leaf fallback instead).

## 5. Summary of backport-specific runtime differences vs upstream v0.10.4

| Area | Upstream (>= 3.10) | Backport on 3.8/3.9 |
| --- | --- | --- |
| Peer chain source | `get_unverified_chain()` (full server chain) | `getpeercert(binary_form=True)` (leaf only) |
| Chain building | OS verifies the server-sent chain | OS builds the chain itself (Windows AIA / macOS network fetch on 10.9+) |
| macOS CERT_NONE | tolerates `errSecNotTrusted` / `errSecCertificateExpired` only | tolerates any SecTrust failure |
| `requires-python` | `>= 3.10` | `>= 3.8` |
| Import on 3.8/3.9 | `ImportError` gate in `__init__.py` | imports normally, leaf fallback used |
