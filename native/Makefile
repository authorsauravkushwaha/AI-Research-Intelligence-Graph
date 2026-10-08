# NEXUS native graph analytics kernel
#
#   make            -> build/bin/nexus-kernel
#   make test       -> run the built-in self-test suite
#   make bench      -> synthetic 20k-node benchmark
#
# Zero third-party dependencies: a C++17 compiler is the only requirement.
CXX      ?= g++
CXXFLAGS ?= -std=c++17 -O3 -funroll-loops -DNDEBUG -Wall -Wextra -Wno-unused-parameter
VERSION  ?= 1.0.0
BUILD    := build
BIN      := $(BUILD)/bin/nexus-kernel
SRC      := src/main.cpp src/algorithms.cpp
HDR      := src/graph.hpp src/minijson.hpp

CPPFLAGS += -DNEXUS_VERSION=\"$(VERSION)\"

.PHONY: all clean test bench install

all: $(BIN)

$(BIN): $(SRC) $(HDR)
	@mkdir -p $(BUILD)/bin
	$(CXX) $(CXXFLAGS) $(CPPFLAGS) -Isrc -o $@ $(SRC) -lm
	@echo "built $@"

test: $(BIN)
	@bash test/run_tests.sh

bench: $(BIN)
	@bash test/bench.sh

clean:
	rm -rf $(BUILD)

install: $(BIN)
	install -d $(DESTDIR)/usr/local/bin
	install -m 0755 $(BIN) $(DESTDIR)/usr/local/bin/nexus-kernel
