#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#define N 1536
#define M 6912
int cl[M][3],a[N+1],nt[M],*occ[2*N+2],oc[2*N+2],unsat[M],pos[M],nu,pc[N+1],ngc[N+1];
int lit_true(int l){return l>0?a[l]:!a[-l];}
int idx(int l){return l>0?2*l:2*(-l)+1;}
void add(int c){pos[c]=nu;unsat[nu++]=c;} void rem(int c){int p=pos[c];unsat[p]=unsat[--nu];pos[unsat[p]]=p;}
int brk(int v){int l=a[v]?v:-v,b=0,i;for(i=0;i<oc[idx(l)];i++)if(nt[occ[idx(l)][i]]==1)b++;return b;}
void flip(int v){int l=a[v]?v:-v,i,c;a[v]=!a[v];
 for(i=0;i<oc[idx(l)];i++){c=occ[idx(l)][i];if(--nt[c]==0)add(c);}
 for(i=0;i<oc[idx(-l)];i++){c=occ[idx(-l)][i];if(nt[c]++==0)rem(c);}}
int main(int argc,char**argv){FILE*f=fopen("cnf.txt","r");int i,j,seed=atoi(argv[1]);srand(seed);
 for(i=0;i<M;i++)for(j=0;j<3;j++){fscanf(f,"%d",&cl[i][j]);int l=cl[i][j];oc[idx(l)]++;if(l>0)pc[l]++;else ngc[-l]++;}
 for(i=0;i<2*N+2;i++){occ[i]=malloc(sizeof(int)*(oc[i]+1));oc[i]=0;}
 for(i=0;i<M;i++)for(j=0;j<3;j++){int x=idx(cl[i][j]);occ[x][oc[x]++]=i;}
 for(i=1;i<=N;i++)a[i]=pc[i]>ngc[i]?1:pc[i]<ngc[i]?0:rand()&1;
 nu=0;for(i=0;i<M;i++){nt[i]=0;for(j=0;j<3;j++)nt[i]+=lit_true(cl[i][j]);if(!nt[i])add(i);}
 fprintf(stderr,"init unsat %d\n",nu);
 time_t t0=time(0);long fl=0;int best=M;double p=0.567;
 while(nu&&time(0)-t0<atoi(argv[2])){int c=unsat[rand()%nu],bv=0,bb=99,k;
  int b[3];for(k=0;k<3;k++){b[k]=brk(abs(cl[c][k]));if(b[k]<bb){bb=b[k];bv=abs(cl[c][k]);}}
  if(bb>0&&rand()<p*RAND_MAX)bv=abs(cl[c][rand()%3]);
  flip(bv);fl++;if(nu<best){best=nu;}
  if(fl%20000000==0)fprintf(stderr,"%lds best %d\n",time(0)-t0,best);}
 if(!nu){printf("SAT ");for(i=1;i<=N;i++)putchar('0'+a[i]);printf("\n");}else printf("FAIL best %d\n",best);
}
