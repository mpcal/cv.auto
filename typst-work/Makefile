.PHONY: all watch clean

PDF = output/resume.pdf

all: $(PDF)

$(PDF): main.typ resume.json avatar.jpg Makefile $(wildcard fonts/fontawesome/*.otf)
	@mkdir -p output
	./typst compile --font-path fonts --font-path ../src/cvopt/resources/fonts main.typ $(PDF)
	@echo "Compiled successfully to $(PDF)"

watch:
	@mkdir -p output
	./typst watch --font-path fonts --font-path ../src/cvopt/resources/fonts main.typ $(PDF)

clean:
	rm -rf output
