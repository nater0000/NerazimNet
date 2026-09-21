"""htpasswd-compatible password hashing (Apache apr1-md5).

apr1 is supported natively by Nginx auth_basic on every platform (no
libcrypt dependency) and requires no third-party packages. It is not a
strong modern KDF, but it is the standard portable htpasswd format and is
appropriate for edge protection in front of private tooling.
"""
import hashlib
import secrets

_ITOA64 = './0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'


def _to64(value: int, length: int) -> str:
    out = ''
    for _ in range(length):
        out += _ITOA64[value & 0x3F]
        value >>= 6
    return out


def apr1_md5(password: str, salt: str) -> str:
    """Returns the $apr1$<salt>$<hash> digest for a plaintext password."""
    salt = salt[:8]
    magic = b'$apr1$'
    pw = password.encode('utf-8')
    salt_b = salt.encode('ascii')

    ctx = hashlib.md5()
    ctx.update(pw + magic + salt_b)
    final = hashlib.md5(pw + salt_b + pw).digest()

    for i in range(len(pw)):
        ctx.update(final[i % 16:i % 16 + 1])

    i = len(pw)
    while i:
        ctx.update(b'\x00' if i & 1 else pw[0:1])
        i >>= 1

    final = ctx.digest()

    for i in range(1000):
        ctx = hashlib.md5()
        ctx.update(pw if i & 1 else final)
        if i % 3:
            ctx.update(salt_b)
        if i % 7:
            ctx.update(pw)
        ctx.update(final if i & 1 else pw)
        final = ctx.digest()

    encoded = (
        _to64((final[0] << 16) | (final[6] << 8) | final[12], 4)
        + _to64((final[1] << 16) | (final[7] << 8) | final[13], 4)
        + _to64((final[2] << 16) | (final[8] << 8) | final[14], 4)
        + _to64((final[3] << 16) | (final[9] << 8) | final[15], 4)
        + _to64((final[4] << 16) | (final[10] << 8) | final[5], 4)
        + _to64(final[11], 2)
    )
    return f'$apr1${salt}${encoded}'


def htpasswd_line(username: str, password: str, salt: str = None) -> str:
    """Returns a 'user:$apr1$...' htpasswd file line."""
    if salt is None:
        salt = ''.join(secrets.choice(_ITOA64) for _ in range(8))
    return f'{username}:{apr1_md5(password, salt)}'
