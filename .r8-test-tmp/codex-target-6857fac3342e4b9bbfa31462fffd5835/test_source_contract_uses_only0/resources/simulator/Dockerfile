FROM docker.io/library/ubuntu:22.04@sha256:45e9ddc6895604674618a409ca9f73d301da8fdb75fe330e8e9a27b3b219e4ad AS buildenv
# Adapted from mmwave-LENA-oran's ns3_docker/Dockerfile at the commit below.
ARG log_level_e2sim=2
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y \
    software-properties-common build-essential git cmake libsctp-dev autoconf automake \
    libtool bison flex libboost-all-dev g++ python3 python3-pip python3-distutils python3-apt && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
COPY oran-lab-resources /workspace/oran-lab-resources
RUN git init e2sim && cd e2sim && \
    git fetch --depth 1 https://github.com/MinaYonan123/e2sim-kpmv3-RCv1.03.git acf4f6b2baa8c645af566ea210146abd97de1f48 && \
    git checkout --detach acf4f6b2baa8c645af566ea210146abd97de1f48 && \
    mkdir -p e2sim/build
WORKDIR /workspace/e2sim/e2sim/build
RUN cmake .. -DDEV_PKG=1 -DLOG_LEVEL=${log_level_e2sim} && \
    make package && dpkg --install ./e2sim-dev_1.0.0_amd64.deb && ldconfig

WORKDIR /workspace
RUN git init ns3-mmwave-oran && cd ns3-mmwave-oran && \
    git fetch --depth 1 https://github.com/MinaYonan123/mmwave-LENA-oran.git f2313c286eda2b0f833f089a2eb1c91dea578685 && \
    git checkout --detach f2313c286eda2b0f833f089a2eb1c91dea578685 && \
    git config submodule.contrib/oran-interface.url https://github.com/MinaYonan123/oran-interface && \
    git config submodule.src/nr.url https://github.com/MinaYonan123/ns3-oran-lena-nr.git && \
    git submodule update --init --depth 1 -- contrib/oran-interface src/nr && \
    test "$(git -C contrib/oran-interface rev-parse HEAD)" = 0eedb5ff8e3597e88356b0af005ad144344c6f38 && \
    test "$(git -C src/nr rev-parse HEAD)" = 09a044894e8aa1025cca2e2984ad2431bdcd6c40 && \
    mkdir -p .oran-lab-disabled-scratch && \
    for file in cttc-nr-demo.cc orange-rf-channel-reconfiguration.cc rf-channel-reconfiguration-scenario.cc; do \
        if [ -f "scratch/$file" ]; then mv "scratch/$file" .oran-lab-disabled-scratch/ || exit 1; fi; \
    done
WORKDIR /workspace/ns3-mmwave-oran
RUN find . -type f \( -name '*.sh' -o -name '*.py' -o -name Makefile -o -name CMakeLists.txt -o -name '*.cmake' \) -exec sed -i 's/\r$//' {} + && \
    chmod +x ./ns3 && \
    install -D -m 0644 /workspace/oran-lab-resources/my_energy_scenario_optimized.cc scratch/my_energy_scenario_optimized.cc && \
    ./ns3 configure && ./ns3 build

WORKDIR /workspace
RUN add-apt-repository ppa:ubuntu-toolchain-r/test -y && \
    apt-get update && apt-get install -y gcc-13 g++-13 && \
    update-alternatives --install /usr/bin/gcc gcc /usr/bin/gcc-13 100 --slave /usr/bin/g++ g++ /usr/bin/g++-13
RUN apt-get remove -y asn1c && apt-get update && apt-get install -y git autoconf automake libtool make gcc g++ m4 && \
    git init /tmp/asn1c && cd /tmp/asn1c && \
    git fetch --depth 1 https://github.com/mouse07410/asn1c 940dd5fa9f3917913fd487b13dfddfacd0ded06e && \
    git checkout --detach 940dd5fa9f3917913fd487b13dfddfacd0ded06e && \
    autoreconf -iv && ./configure --prefix=/opt/asn1c && make -j"$(nproc)" && make install
ENV PATH="/opt/asn1c/bin:${PATH}"
RUN git init flexric && cd flexric && \
    git fetch --depth 1 https://gitlab.eurecom.fr/mosaic5g/flexric.git 18c3325091ad6890a706ebb2bba38d3c0fb741e5 && \
    git checkout --detach 18c3325091ad6890a706ebb2bba38d3c0fb741e5 && \
    xapp_dir=examples/xApp/c/orange && \
    if [ ! -d "$xapp_dir" ]; then xapp_dir=examples/xApp/c/myxapp; fi && \
    test -d "$xapp_dir" && \
    install -m 0644 /workspace/oran-lab-resources/xapp_my_energy_optimized.c "$xapp_dir/xapp_my_energy_optimized.c" && \
    sed -i 's/\r$//' "$xapp_dir/xapp_my_energy_optimized.c" && \
    if ! grep -Fq 'add_executable(xapp_my_energy_optimized' "$xapp_dir/CMakeLists.txt"; then \
      printf '\nadd_executable(xapp_my_energy_optimized\n    xapp_my_energy_optimized.c\n    ${CMAKE_SOURCE_DIR}/src/util/alg_ds/alg/defer.c\n)\n\ntarget_link_libraries(xapp_my_energy_optimized\n    PUBLIC\n    e42_xapp\n    pthread\n    sctp\n    dl\n)\n' >> "$xapp_dir/CMakeLists.txt"; \
    fi && \
    cmake -S . -B build -DE2AP_VERSION=E2AP_V1 -DKPM_VERSION=KPM_V3_00 && \
    cmake --build build -j"$(nproc)" && cmake --install build && ldconfig
WORKDIR /workspace
CMD ["/bin/sh"]
