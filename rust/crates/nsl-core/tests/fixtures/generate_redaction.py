"""Observe default redact_text rules in frozen Python Oracle 28a949e."""
from itertools import product
from pathlib import Path
import random
import sys
sys.path.insert(0, str(Path(sys.argv[1]).resolve()))
from nsl.data_protection import redact_text

def enc(text):
    return text.encode().hex() or "-"

cases = ["", "x", "[REDACTED]", "가😀", "Bearer synthetic-fixture",
         "api_key=synthetic-fixture", "authorization: Bearer sample", "secret='two words'",
         'token="one\'two"', "token='one\"two'", "token='a\rb'", 'token="a\nb"']
labels = ("access_key", "access-key", "accesskey", "ACCESS_TOKEN", "api_key", "api-key",
          "authorization", "client_secret", "cookie", "credential", "credentials", "id-token",
          "password", "passwd", "private-key", "pwd", "refresh_token", "secret", "session-token", "token",
          "tokenish", "bad-token", "api--key", "API_KEY", "ſecret", "prİvate_key")
spaces = ("", " ", "\t", "\r\n", "\x1c", "\u0085", "\u00a0", "\u2028", "\u200b")
values = ("", "x", "'two words'", '"two words"', "'", '"', "Bearer one", "Basic\txyz==",
          "Bearer", "Bearer ,", "Basic ;", "x,y", "x;y", "x]y", "x}y", "x:y", "가😀")
for label,space,value in product(labels,spaces,values):
    cases.append(f"before {label}{space}={space}{value}; after")
for prefix,scheme,space,token in product(("", "a", "_", "한", "😀", "\u0301", "\U00011f02"),
        ("Bearer", "Basic", "bAsIc", "BAſİC", "bearers", "Basic-"), spaces,
        ("a+/==", "", "!", "İıſK", "가", "x,y")):
    cases.append(f"{prefix}{scheme}{space}{token}, tail")
for begin,end in product(("", " RSA", " RSA 1", " lowercase", "_RSA", " RSA-", "  "), repeat=2):
    cases.append(f"before -----BEGIN{begin} PRIVATE KEY-----\nsynthetic\n-----END{end} PRIVATE KEY----- after")
cases += [
 "-----BEGIN PRIVATE KEY-----unterminated",
 "-----BEGIN PRIVATE KEY-----nested -----BEGIN RSA PRIVATE KEY-----x-----END PRIVATE KEY----- tail -----END RSA PRIVATE KEY-----",
 "-----BEGIN PRIVATE KEY-----x-----END PRIVATE KEY----- ; -----BEGIN EC PRIVATE KEY-----y-----END EC PRIVATE KEY-----",
 "secret='Bearer one' Bearer two token=eyJx.y.z",
]
for prefix,first,second,third,suffix in product(("", "a", "_", "-", ".", "가", "😀"),
        ("", "x", "-", "가"), ("", "x", "-", "가"), ("", "x", "-", "가"), ("", ".", "_", "가")):
    cases.append(f"{prefix}eyJ{first}.{second}.{third}{suffix}")
rng=random.Random(280949)
parts=("Bearer", "Basic", "api_key", "token", "secret", "eyJ", "x.y.z", "'", '"', "=", ":", ";", ",", "]", "}", " ", "\n", "\x1f", "İ", "ſ", "가", "😀")
for _ in range(2000):
    cases.append("".join(rng.choice(parts) for _ in range(rng.randrange(0,25))))
print('# Default redact_text; Python Oracle 28a949ec96581cbdc52c8133527b23bb5b72ee0a')
for index,text in enumerate(dict.fromkeys(cases)):
    print(index,enc(text),enc(redact_text(text)),sep='\t')
