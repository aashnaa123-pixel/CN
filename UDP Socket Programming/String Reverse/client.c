
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
#pragma comment(lib, "ws2_32.lib")
#else
#include <unistd.h>
#include <sys/types.h>
#include <sys/socket.h>
#include <arpa/inet.h>
#include <netinet/in.h>
#endif

#define PORT 8080
#define MAXLINE 1024

int main()
{
    SOCKET sockfd;
    char buffer[MAXLINE];
    struct sockaddr_in servaddr;
    socklen_t len;
    int n;

#ifdef _WIN32
    WSADATA wsaData;
    if (WSAStartup(MAKEWORD(2, 2), &wsaData) != 0)
    {
        perror("WSAStartup failed");
        exit(EXIT_FAILURE);
    }
#endif

    
    sockfd = socket(AF_INET, SOCK_DGRAM, 0);

    if (sockfd < 0)
    {
        perror("Socket creation failed");
        exit(EXIT_FAILURE);
    }

    memset(&servaddr, 0, sizeof(servaddr));

    // Configure server address
    servaddr.sin_family = AF_INET;
    servaddr.sin_port = htons(PORT);
    servaddr.sin_addr.s_addr = inet_addr("127.0.0.1");

    
    printf("Enter a string: ");
    fgets(buffer, MAXLINE, stdin);

  
    buffer[strcspn(buffer, "\n")] = '\0';

    
    if (sendto(sockfd, buffer, strlen(buffer), 0,
               (struct sockaddr *)&servaddr,
               sizeof(servaddr)) < 0)
    {
        perror("Send failed");
        close(sockfd);
        exit(EXIT_FAILURE);
    }

    printf("String sent to server.\n");

    
    len = sizeof(servaddr);

    n = recvfrom(sockfd, buffer, MAXLINE - 1, 0,
                 (struct sockaddr *)&servaddr, &len);

    if (n < 0)
    {
        perror("Receive failed");
        close(sockfd);
        exit(EXIT_FAILURE);
    }

    buffer[n] = '\0';

    printf("Reversed string from server: %s\n", buffer);

    close(sockfd);

    return 0;
}
