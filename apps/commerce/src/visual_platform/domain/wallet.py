from dataclasses import dataclass, field


@dataclass
class Wallet:
    """Reference domain model only; production mutations require PG transactions."""

    available: int
    reserved: int = 0
    funded: int = field(init=False)
    spent: int = 0
    holds: dict = field(default_factory=dict)

    def __post_init__(self):
        if type(self.available) is not int or self.available < 0:
            raise ValueError("invalid credits")
        self.funded = self.available

    def freeze(self, task, amount):
        if type(amount) is not int or amount <= 0:
            raise ValueError("invalid amount")
        if task in self.holds:
            if self.holds[task][0] != amount:
                raise ValueError("conflict")
            return
        if self.available < amount:
            raise ValueError("insufficient credits")
        self.available -= amount
        self.reserved += amount
        self.holds[task] = (amount, "frozen")

    def finish(self, task, outcome):
        if outcome not in ("charged", "refunded"):
            raise ValueError("invalid outcome")
        amount, state = self.holds[task]
        if state == outcome:
            return
        if state != "frozen":
            raise ValueError("terminal conflict")
        self.reserved -= amount
        if outcome == "charged":
            self.spent += amount
        else:
            self.available += amount
        self.holds[task] = (amount, outcome)
        assert self.available + self.reserved == self.funded - self.spent
