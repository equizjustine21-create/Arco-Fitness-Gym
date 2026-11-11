"""Authentication attempt throttling backed by the application database."""

import hashlib
import hmac


class AuthAttemptLimiter:
    MAX_ATTEMPTS = 5
    MAX_IP_ATTEMPTS = 20
    WINDOW_MINUTES = 15
    BLOCK_MINUTES = 15

    def __init__(self, secret_key):
        self.__secret_key = secret_key.encode("utf-8")

    def bucket_keys(self, scope, remote_address, identifier=None):
        address = remote_address or "unknown"
        keys = [self.__digest(f"{scope}\0ip\0{address}")]
        if identifier is not None:
            normalized_identifier = identifier.strip().casefold()
            keys.append(
                self.__digest(
                    f"{scope}\0account\0{address}\0{normalized_identifier}"
                )
            )
        return tuple(keys)

    def is_limited(self, database, bucket_keys):
        for bucket_key in bucket_keys:
            blocked = database.execute(
                """SELECT 1 FROM auth_rate_limits
                   WHERE bucket_key = ? AND blocked_until > UTC_TIMESTAMP()""",
                (bucket_key,),
            ).fetchone()
            if blocked:
                return True
        return False

    def record_failure(self, database, bucket_keys):
        database.execute(
            """DELETE FROM auth_rate_limits
               WHERE window_started_at < DATE_SUB(UTC_TIMESTAMP(), INTERVAL 1 DAY)"""
        )
        for index, bucket_key in enumerate(bucket_keys):
            max_attempts = (
                self.MAX_IP_ATTEMPTS if index == 0 else self.MAX_ATTEMPTS
            )
            statement = f"""INSERT INTO auth_rate_limits
                            (bucket_key, window_started_at, attempt_count, blocked_until)
                            VALUES (?, UTC_TIMESTAMP(), 1, NULL)
                            ON DUPLICATE KEY UPDATE
                              attempt_count = IF(
                                window_started_at <= DATE_SUB(
                                  UTC_TIMESTAMP(), INTERVAL {self.WINDOW_MINUTES} MINUTE
                                ),
                                1,
                                attempt_count + 1
                              ),
                              blocked_until = IF(
                                window_started_at <= DATE_SUB(
                                  UTC_TIMESTAMP(), INTERVAL {self.WINDOW_MINUTES} MINUTE
                                ),
                                NULL,
                                IF(
                                  attempt_count >= {max_attempts},
                                  DATE_ADD(
                                    UTC_TIMESTAMP(), INTERVAL {self.BLOCK_MINUTES} MINUTE
                                  ),
                                  blocked_until
                                )
                              ),
                              window_started_at = IF(
                                window_started_at <= DATE_SUB(
                                  UTC_TIMESTAMP(), INTERVAL {self.WINDOW_MINUTES} MINUTE
                                ),
                                UTC_TIMESTAMP(),
                                window_started_at
                              )"""
            database.execute(statement, (bucket_key,))
        database.commit()

    def clear_account_bucket(self, database, bucket_keys):
        if len(bucket_keys) > 1:
            database.execute(
                "DELETE FROM auth_rate_limits WHERE bucket_key = ?",
                (bucket_keys[1],),
            )
            database.commit()

    def __digest(self, value):
        return hmac.new(
            self.__secret_key,
            value.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
