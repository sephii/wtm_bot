import enum
from typing import List


class Justify(enum.Enum):
    LEFT = "left"
    RIGHT = "right"
    CENTER = "center"


class Heading:
    def __init__(self, label: str, justify: Justify = Justify.LEFT):
        self.label = label
        self.justify = justify


class Table:
    def __init__(self, headings: List[Heading]):
        self.headings = headings
        self.padding = 1
        self.rows = []
        self._maxlen = []

    def __str__(self) -> str:
        return self.as_str()

    def add_row(self, *args):
        self.rows.append(args)

        for pos, arg in enumerate(args):
            arg_len = len(str(arg))
            try:
                self._maxlen[pos] = max(
                    self._maxlen[pos], arg_len, len(self.headings[pos].label)
                )
            except IndexError:
                self._maxlen.append(max(arg_len, len(self.headings[pos].label)))

    def col_width(self, col_number: int) -> int:
        try:
            col_width = self._maxlen[col_number]
        except IndexError:
            col_width = 0

        return col_width + self.padding * 2

    def as_str(self) -> str:
        padding = " " * self.padding
        buff = "┏"

        for col_number, col in enumerate(self.headings):
            buff += "━" * self.col_width(col_number)
            buff += "┳" if col_number < len(self.headings) - 1 else "┓"

        buff += "\n"

        for col_number, heading in enumerate(self.headings):
            label = heading.label.ljust(self._maxlen[col_number])
            buff += f"┃{padding}{label}{padding}"

        buff += "┃\n"
        buff += "┡"

        for col_number, col in enumerate(self.headings):
            buff += "━" * self.col_width(col_number)
            buff += "╇" if col_number < len(self.headings) - 1 else "┩"

        buff += "\n"

        for row in self.rows:
            for col_number, col in enumerate(row):
                heading = self.headings[col_number]
                just_func = (
                    col.ljust
                    if heading.justify == Justify.LEFT
                    else col.rjust if heading.justify == Justify.RIGHT else col.center
                )
                label = just_func(self._maxlen[col_number])

                buff += f"│{padding}{label}{padding}"

            buff += "│\n"

        buff += "└"

        for col_number, col in enumerate(self.headings):
            buff += "─" * self.col_width(col_number)
            buff += "┴" if col_number < len(self.headings) - 1 else "┘"

        return buff
