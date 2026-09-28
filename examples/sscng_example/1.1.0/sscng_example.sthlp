{smcl}
{* *! version 1.1.0 27sep2026}{...}
{title:Title}

{p 4 4 2}
{cmd:sscng_example} {hline 2} Double a value and add one in the SSC-NG local pilot.

{title:Syntax}

{p 8 12 2}
{cmd:sscng_example,} {opt value(#)}

{title:Description}

{p 4 4 2}
Version 1.1.0 calls sscng_helper version 0.1.0 to double the supplied value,
then adds one. It requires that helper and Stata 16 or later.
Unlike version 1.0.0, value(2) therefore returns 5 instead of 4.
This intentional change makes historical releases easy to distinguish.
It is an arithmetic test fixture, not a research method.

{title:Example}

{p 8 12 2}{cmd:. sscng_example, value(2)}
{p 8 12 2}{cmd:. display r(result)}
{p 8 12 2}5

{title:Stored results}

{p 4 4 2}{cmd:r(result)} contains twice the supplied value plus one.
