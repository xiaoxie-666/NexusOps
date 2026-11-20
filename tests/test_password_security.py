import hashlib
import unittest

from app.core.security import hash_password, verify_password


class PasswordSecurityTests(unittest.TestCase):
    def test_new_hash_is_salted_and_verifiable(self):
        first = hash_password("correct-horse-battery")
        second = hash_password("correct-horse-battery")
        self.assertTrue(first.startswith("pbkdf2_sha256$"))
        self.assertNotEqual(first, second)
        self.assertTrue(verify_password("correct-horse-battery", first))
        self.assertFalse(verify_password("wrong", first))

    def test_legacy_sha256_hash_remains_verifiable(self):
        legacy = hashlib.sha256("legacy-password".encode()).hexdigest()
        self.assertTrue(verify_password("legacy-password", legacy))
        self.assertFalse(verify_password("wrong-password", legacy))


if __name__ == "__main__":
    unittest.main()
