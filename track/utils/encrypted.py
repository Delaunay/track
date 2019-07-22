import socket

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding


class EncryptedSocket(socket.socket):
    """Socket with an encrypted layer"""

    def __init__(self, *args, **kwargs):
        raise TypeError(f"{self.__class__.__name__} does not have a public constructor.")

    @classmethod
    def _create(cls, sock, server_side=False, handshaked=False):
        kwargs = dict(
            family=sock.family, type=sock.type, proto=sock.proto,
            fileno=sock.fileno()
        )
        self = cls.__new__(cls, **kwargs)
        super(EncryptedSocket, self).__init__(**kwargs)
        self.settimeout(sock.gettimeout())
        sock.detach()

        self.cipher = None
        self.message_size = None
        self.message_received = None
        self.server_side = server_side

        # do the handshake if client
        if not self.server_side and not handshaked:
            self._handshake()

        return self

    def _handshake(self):
        """Open a socket to address, port and initialize the encryption layer by exchanging a key using X25519.
        The key is used as an AES key throughout the communication.

        Returns
        -------
        return itself
        """
        private_key = X25519PrivateKey.generate()
        pubkey = private_key.public_key().public_bytes(
            encoding=Encoding.Raw,
            format=PublicFormat.Raw
        )

        # send client public key
        super().send(pubkey)

        # receive server public Key
        data = super().recv(32)

        # from public key get shared_key
        server_key = X25519PublicKey.from_public_bytes(data)
        shared_key = private_key.exchange(server_key)

        key = HKDF(
            algorithm=hashes.SHA256(),
            length=48,
            salt=None,
            info=b'handshake data',
            backend=default_backend()
        ).derive(shared_key)

        self.cipher = Cipher(
            algorithms.AES(key[0:32]),
            modes.CBC(key[32:]),
            backend=default_backend()
        )

        return self

    def accept(self):
        """Accept an incoming connection & initialize the encryption layer for that client

        Returns
        -------
        returns (socket, addr) of the client
        """

        clt, addr = super().accept()

        # Generate a private key
        server_key = X25519PrivateKey.generate()

        pubkey = server_key.public_key().public_bytes(
            encoding=Encoding.Raw,
            format=PublicFormat.Raw
        )

        data = clt.recv(32)   # Receive client public Key
        clt.sendall(pubkey)    # send public key to client

        public_key = X25519PublicKey.from_public_bytes(data)

        # get Shared key
        shared_key = server_key.exchange(public_key)
        shared_key = HKDF(
            algorithm=hashes.SHA256(),
            length=48,
            salt=None,
            info=b'handshake data',
            backend=default_backend()
        ).derive(shared_key)

        encrypted_socket = wrap_socket(clt, False, handshaked=True)
        encrypted_socket.cipher = Cipher(
            algorithms.AES(shared_key[0:32]),
            modes.CBC(shared_key[32:]),
            backend=default_backend()
        )

        return encrypted_socket, addr

    def send(self, data: bytes, flags: int = 0) -> int:
        self.sendall(data, flags)
        return len(data)

    def sendall(self, data, flags: int = 0):
        if isinstance(data, bytearray):
            data = bytes(data)

        encrypt = self.cipher.encryptor()
        padder = padding.PKCS7(128).padder()

        padded_bytes = padder.update(data)
        padded_bytes += padder.finalize()

        encrypted = encrypt.update(padded_bytes)
        encrypted += encrypt.finalize()

        super().sendall(encrypted, flags)
        return len(data)

    def readsize(self):
        decrypt = self.cipher.decryptor()
        unpadder = padding.PKCS7(128).unpadder()

        size = super().recv(4)
        return size, (decrypt, unpadder)

    def recv(self, buffersize, flags: int = 0, context=None):
        data = super().recv(buffersize, flags)

        # no data nothing to decrypt
        if not data:
            return data

        # ----
        if context is None:
            decrypt = self.cipher.decryptor()
            unpadder = padding.PKCS7(128).unpadder()
        else:
            decrypt, unpadder = context
        # ----

        decrypted = decrypt.update(data)

        while True:
            try:
                decrypted += decrypt.finalize()
                break

            except ValueError:
                data = super().recv(buffersize, flags)
                decrypted += decrypt.update(data)

        unpadded = unpadder.update(decrypted)
        unpadded += unpadder.finalize()

        return unpadded


def wrap_socket(sock, server_side=False, handshaked=False):
    return EncryptedSocket._create(
        sock=sock,
        server_side=server_side,
        handshaked=handshaked
    )
