"""Everything about commissioning more than one terminal at a time.

A profile describes a machine. A roster describes where that machine sits on a
production line and what stopping it costs, which is the part that decides the
order the bench works in.

The split inside this package is between reading a roster and questioning one.
:mod:`~v4t_setup.fleet.roster` refuses a document it cannot turn into a roster.
:mod:`~v4t_setup.fleet.audit` takes one that parsed and asks what is wrong with
it anyway, which always needs a second document to answer.
"""
